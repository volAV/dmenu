#define main dmenu_application_main
#include "../dmenu.c"
#undef main

#include <inttypes.h>

#define GUARD_SIZE 32
#define CASE_GUARD_MS 2000

static unsigned char storage[PREVIEWMAX + 1 + GUARD_SIZE];
static pid_t active_pgid;
static int observed_exit_status = -1;
static int64_t case_started;
static struct item selected;
static const unsigned char guard[GUARD_SIZE] = {
	0xa5, 0x5a, 0xa5, 0x5a, 0xa5, 0x5a, 0xa5, 0x5a,
	0xa5, 0x5a, 0xa5, 0x5a, 0xa5, 0x5a, 0xa5, 0x5a,
	0xa5, 0x5a, 0xa5, 0x5a, 0xa5, 0x5a, 0xa5, 0x5a,
	0xa5, 0x5a, 0xa5, 0x5a, 0xa5, 0x5a, 0xa5, 0x5a
};

static void
fail(const char *message)
{
	pid_t child = preview.pid;
	pid_t pgid = active_pgid ? active_pgid : preview.pgid;

	fprintf(stderr, "FAIL preview-boundary: %s\n", message);
	if (pgid > 0)
		kill(-pgid, SIGKILL);
	else if (child > 0)
		kill(child, SIGKILL);
	previewclose(&preview.input);
	previewclose(&preview.output);
	if (child > 0)
		while (waitpid(child, NULL, 0) < 0 && errno == EINTR)
			;
	exit(1);
}

static unsigned char
pattern(size_t i)
{
	static const unsigned char special[] = { 0, 0xc3, 0x28, 0xf0, 0x9f, 0x92 };
	return i < sizeof special ? special[i] : (unsigned char)((i * 131u + i / 251u) & 0xff);
}

static uint64_t
hashbytes(const unsigned char *data, size_t len)
{
	uint64_t hash = UINT64_C(14695981039346656037);
	size_t i;

	for (i = 0; i < len; i++) {
		hash ^= data[i];
		hash *= UINT64_C(1099511628211);
	}
	return hash;
}

static int
helper(void)
{
	char *end, *value = getenv("PREVIEW_TEST_BYTES");
	size_t len, offset = 0, input_used = 0, i;
	unsigned char chunk[4096];
	char input[128];
	const char expected[] = "synthetic selected text with spaces\n";
	int exit_status = atoi(getenv("PREVIEW_TEST_EXIT") ? getenv("PREVIEW_TEST_EXIT") : "0");
	ssize_t n;

	if (!value || !(len = strtoul(value, &end, 10)) || *end || len > PREVIEWMAX + 1)
		return 90;
	while ((n = read(STDIN_FILENO, input + input_used, sizeof input - input_used)) > 0) {
		input_used += n;
		if (input_used == sizeof input)
			return 92;
	}
	if (n < 0 || input_used != sizeof expected - 1 ||
	    memcmp(input, expected, sizeof expected - 1))
		return 93;
	if (getenv("PREVIEW_TEST_EOF_ALIVE")) {
		close(STDOUT_FILENO);
		sleep(5);
		return 0;
	}
	if (getenv("PREVIEW_TEST_PARTIAL"))
		len = MIN(len, 137);
	while (offset < len) {
		size_t n = MIN(sizeof chunk, len - offset);
		for (i = 0; i < n; i++)
			chunk[i] = pattern(offset + i);
		for (i = 0; i < n;) {
			ssize_t written = write(STDOUT_FILENO, chunk + i, n - i);
			if (written < 0 && errno == EINTR)
				continue;
			if (written <= 0)
				return 91;
			i += written;
		}
		offset += n;
	}
	if (len == PREVIEWMAX + 1 && exit_status == 0)
		sleep(5);
	return exit_status;
}

static void
check_guard(void)
{
	if (memcmp(storage + PREVIEWMAX + 1, guard, sizeof guard))
		fail("buffer canary changed");
}

static void
settle_child(pid_t pgid)
{
	/* Starts before previewstart, so the two-second budget includes teardown. */
	int64_t end = case_started + CASE_GUARD_MS;

	if (previewtime() >= end)
		fail("cleanup observation began after its anchored deadline");

	while (preview.pid && previewtime() < end) {
		previewreap();
		if (preview.pid)
			usleep(1000);
	}
	if (preview.pid)
		fail("child was not reaped before the case guard");
	if (pgid > 0) {
		while (kill(-pgid, 0) == 0 && previewtime() < end)
			usleep(1000);
		if (kill(-pgid, 0) == 0 || errno != ESRCH)
			fail("owned process group remains alive");
	}
}

static void
start_case(int bytes, int exit_status)
{
	/*
	 * Sanitizer runs require an uninstrumented helper because previewstart
	 * redirects child stderr: PREVIEW_TEST_HELPER=/absolute/path/to/normal-probe
	 * /absolute/path/to/sanitized-preview-boundary
	 */
	const char *helper_path = getenv("PREVIEW_TEST_HELPER");
	char count[16], status[16];
	active_pgid = 0;
	observed_exit_status = -1;

	if (!helper_path || helper_path[0] != '/' || !helper_path[1])
		fail("PREVIEW_TEST_HELPER must name an absolute uninstrumented probe executable");
	previewcmd = (char *)helper_path;
	memset(&selected, 0, sizeof selected);
	selected.text = "synthetic selected text with spaces";
	sel = &selected;
	memset(&preview, 0, sizeof preview);
	preview.input = preview.output = -1;
	preview.buf = (char *)storage;
	memset(storage, 0xcc, sizeof storage);
	memcpy(storage + PREVIEWMAX + 1, guard, sizeof guard);
	snprintf(count, sizeof count, "%d", bytes);
	snprintf(status, sizeof status, "%d", exit_status);
	if (setenv("PREVIEW_TEST_BYTES", count, 1) < 0 ||
	    setenv("PREVIEW_TEST_EXIT", status, 1) < 0)
		fail("setenv failed");
	preview.state = PreviewLoading;
	preview.item = &selected;
	preview.generation = preview.running = 1;
	case_started = previewtime();
	previewstart();
	active_pgid = preview.pgid;
	if (preview.state == PreviewUnavailable)
		fail("previewstart failed");
}

static void
run_transfer(void)
{
	int64_t end = case_started + CASE_GUARD_MS;
	struct pollfd fds[2];

	while (preview.deadline && previewtime() < end) {
		previewstep();
		if (!preview.deadline)
			break;
		fds[0] = (struct pollfd){ preview.input, POLLOUT, 0 };
		fds[1] = (struct pollfd){ preview.output, POLLIN, 0 };
		if (poll(fds, 2, 10) < 0 && errno != EINTR)
			fail("poll failed");
		if (preview.input >= 0 && (fds[0].revents & (POLLOUT | POLLHUP | POLLERR)))
			previewwrite();
		if (preview.output >= 0 && (fds[1].revents & (POLLIN | POLLHUP | POLLERR)))
			previewread();
		previewstep();
		check_guard();
	}
	if (preview.deadline)
		fail("internal 2-second case guard expired");
}

static void
check_content(size_t expected, int final_state, int bytes, int exit_status)
{
	pid_t pgid = active_pgid;
	unsigned char *want = malloc(expected ? expected : 1);
	int actual_state = preview.state;
	size_t i, actual_used = preview.used;

	if (!want)
		fail("allocation failed");
	for (i = 0; i < expected; i++)
		want[i] = pattern(i);
	if (preview.state != final_state || preview.used != expected ||
	    memcmp(preview.buf, want, expected) || preview.buf[expected] != '\0')
		fail("unexpected state, retained length, bytes, or terminator");
	settle_child(pgid);
	printf("PASS preview-boundary: %d bytes, configured-exit %d, state %d, used %zu, hash %016" PRIx64
	       " exact-content verified, %" PRId64 " ms\n", bytes, exit_status,
	       actual_state, actual_used, hashbytes(want, expected), previewtime() - case_started);
	if (observed_exit_status >= 0)
		printf("PASS preview-boundary: observed child exit status %d before overflow read\n",
		       observed_exit_status);
	free(want);
}

static void
run_content_case(int bytes, int exit_status, size_t expected, int state)
{
	start_case(bytes, exit_status);
	run_transfer();
	check_content(expected, state, bytes, exit_status);
}
static void
run_overflow_nonzero(void)
{
	int64_t end;
	struct pollfd fds[2];
	siginfo_t info;

	start_case(PREVIEWMAX + 1, 7);
	end = case_started + CASE_GUARD_MS;
	while (preview.used < PREVIEWMAX && preview.deadline && previewtime() < end) {
		previewstep();
		fds[0] = (struct pollfd){ preview.input, POLLOUT, 0 };
		fds[1] = (struct pollfd){ preview.output, POLLIN, 0 };
		if (poll(fds, 2, 10) < 0 && errno != EINTR)
			fail("poll failed while filling preview buffer");
		if (preview.input >= 0 && (fds[0].revents & (POLLOUT | POLLHUP | POLLERR)))
			previewwrite();
		if (preview.output >= 0 && (fds[1].revents & (POLLIN | POLLHUP | POLLERR)))
			previewread();
		check_guard();
	}
	if (preview.used != PREVIEWMAX || !preview.deadline)
		fail("did not retain exactly the preview limit before overflow");
	for (;;) {
		info = (siginfo_t){0};
		if (waitid(P_PID, preview.pid, &info, WEXITED | WNOHANG | WNOWAIT) < 0) {
			if (errno == EINTR)
				continue;
			fail("waitid failed before overflow byte");
		}
		if (info.si_pid)
			break;
		if (previewtime() >= end)
			fail("helper did not exit before overflow byte");
		usleep(1000);
	}
	if (info.si_code != CLD_EXITED || info.si_status != 7)
		fail("helper did not exit 7 before overflow byte");
	observed_exit_status = info.si_status;
	previewread();
	if (preview.state != PreviewTruncated)
		fail("overflow byte did not select Truncated after observed exit 7");
	check_content(PREVIEWMAX, PreviewTruncated, PREVIEWMAX + 1, 7);
}

static void
run_failure_case(int partial)
{
	pid_t pgid;

	if (partial && setenv("PREVIEW_TEST_PARTIAL", "1", 1) < 0)
		fail("setenv partial mode failed");
	start_case(65536, 7);
	run_transfer();
	pgid = active_pgid;
	if (preview.state != PreviewUnavailable || preview.used || preview.buf[0])
		fail("nonzero exit did not clear preview");
	check_guard();
	settle_child(pgid);
	printf("PASS preview-boundary: configured-exit 7, partial=%d, state %d, used %zu, content cleared, %" PRId64 " ms\n",
	       partial, preview.state, preview.used, previewtime() - case_started);
	unsetenv("PREVIEW_TEST_PARTIAL");
}

static void
run_eof_alive(void)
{
	int64_t end;
	struct pollfd fds[2];
	pid_t pgid;

	if (setenv("PREVIEW_TEST_EOF_ALIVE", "1", 1) < 0)
		fail("setenv EOF mode failed");
	start_case(1, 0);
	end = case_started + CASE_GUARD_MS;
	while (preview.output >= 0 && previewtime() < end) {
		fds[0] = (struct pollfd){ preview.input, POLLOUT, 0 };
		fds[1] = (struct pollfd){ preview.output, POLLIN, 0 };
		if (poll(fds, 2, 10) < 0 && errno != EINTR)
			fail("poll failed");
		if (preview.input >= 0 && (fds[0].revents & (POLLOUT | POLLHUP | POLLERR)))
			previewwrite();
		if (preview.output >= 0 && (fds[1].revents & (POLLIN | POLLHUP | POLLERR)))
			previewread();
		previewstep();
	}
	if (preview.output >= 0 || preview.state != PreviewLoading || !preview.deadline)
		fail("stdout EOF did not remain Loading while child lived");
	while (preview.deadline && previewtime() < end) {
		previewstep();
		usleep(1000);
	}
	pgid = active_pgid;
	if (preview.state != PreviewTimeout || preview.used || preview.buf[0])
		fail("stdout EOF did not resolve to cleared Timeout");
	end = previewtime() - case_started;
	if (end < 950 || end > 1200)
		fail("stdout EOF timeout was outside the 1000 ms deadline tolerance");
	check_guard();
	settle_child(pgid);
	printf("PASS preview-boundary: stdout EOF stayed Loading until Timeout, state %d, used %zu, %" PRId64 " ms\n",
	       preview.state, preview.used, previewtime() - case_started);
	unsetenv("PREVIEW_TEST_EOF_ALIVE");
}

int
main(void)
{
	if (getenv("PREVIEW_TEST_BYTES"))
		return helper();
	signal(SIGPIPE, SIG_IGN);
	signal(SIGCHLD, SIG_DFL);
	run_content_case(65535, 0, 65535, PreviewReady);
	run_content_case(65536, 0, 65536, PreviewReady);
	run_content_case(65537, 0, 65536, PreviewTruncated);
	run_overflow_nonzero();
	run_failure_case(1);
	run_failure_case(0);
	run_eof_alive();
	return 0;
}
