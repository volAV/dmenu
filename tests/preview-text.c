#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <time.h>
static int profile_metrics, slow_budget_metrics;
static FILE *profilelog;
static struct timespec profile_wallstart, profile_cpustart;
static void *profile_target;
static int profile_first_measure, profile_in_step;
#define main dmenu_application_main
#define drw_fontset_getwidth preview_test_width
#define previewprepare_step preview_test_prepare_step_real
#include "../dmenu.c"
#undef drw_fontset_getwidth
/* Keep preparation calls mapped to the instrumentable production symbol. */
#undef main

/* The include seam renames the public declaration along with its callers. */
unsigned int drw_fontset_getwidth(Drw *, const char *);

static uint64_t
elapsed_ns(struct timespec start, struct timespec end)
{
	return (uint64_t)(end.tv_sec - start.tv_sec) * 1000000000 +
	       end.tv_nsec - start.tv_nsec;
}
static int
preview_test_prepare_step_real(void);
void __attribute__((no_instrument_function))
__cyg_profile_func_enter(void *function, void *caller)
{
	(void)caller;
	if (profilelog && function == profile_target) {
		profile_in_step = 1;
		clock_gettime(CLOCK_MONOTONIC, &profile_wallstart);
		clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &profile_cpustart);
	}
}

void __attribute__((no_instrument_function))
__cyg_profile_func_exit(void *function, void *caller)
{
	struct timespec wallend, cpuend;

	(void)caller;
	if (profilelog && function == profile_target) {
		clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &cpuend);
		profile_in_step = 0;
		clock_gettime(CLOCK_MONOTONIC, &wallend);
		fprintf(profilelog, "S,%" PRIu64 ",%" PRIu64 ",%u,%d\n",
		        elapsed_ns(profile_wallstart, wallend),
		        elapsed_ns(profile_cpustart, cpuend),
		        preview.prepwork, previewprepare_pending());
		fflush(profilelog);
	}
}
#define REPLACEMENT "\xef\xbf\xbd"
static Drw testdrw;
static int slow_metrics;
static char *testraw;

unsigned int
preview_test_width(Drw *context, const char *text)
{
	struct timespec wallstart, wallend, cpustart, cpuend;
	struct timespec delay = { 0, slow_budget_metrics ? 10000000 :
	                          slow_metrics == 2 ? 100000 : 350000 };
	const unsigned char *s = (const unsigned char *)text;
	unsigned int width;

	if (slow_metrics || slow_budget_metrics)
		nanosleep(&delay, NULL);
	if (context != &testdrw) {
		if (!profile_metrics)
			return drw_fontset_getwidth(context, text);
		clock_gettime(CLOCK_MONOTONIC, &wallstart);
		clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &cpustart);
		width = drw_fontset_getwidth(context, text);
		clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &cpuend);
		clock_gettime(CLOCK_MONOTONIC, &wallend);
		fprintf(profilelog, "M,%" PRIu64 ",%" PRIu64 ",%d,%u\n",
		        elapsed_ns(wallstart, wallend), elapsed_ns(cpustart, cpuend),
		        profile_in_step && previewprepare_pending(), width);
		if (profile_in_step && previewprepare_pending() && !profile_first_measure++)
			fflush(profilelog);
		return width;
	}
	if (!*s)
		return 0;
	return s[0] == 'W' ? 4 : s[0] == 'z' ? 0 : (s[0] < 0x80 ? 1 : 2);
}


static void
require(int condition, const char *message)
{
	if (!condition) {
		fprintf(stderr, "FAIL: %s\n", message);
		exit(1);
	}
}

static void
begin(const unsigned char *bytes, size_t length, unsigned int width)
{
	require(length <= PREVIEWMAX, "test input exceeds retained raw cap");
	free(testraw);
	testraw = malloc(length ? length : 1);
	require(testraw != NULL, "cannot allocate exact-length raw test buffer");
	if (length)
		memcpy(testraw, bytes, length);
	preview.buf = testraw;
	preview.used = length;
	preview.state = PreviewReady;
	preview.deadline = 1;
	previewprepare_start(width);
}

static void
finish(void)
{
	while (previewprepare_pending()) {
		previewprepare_step();
		require(preview.prepwork > 0 || !previewprepare_pending(),
		        "pending preparation made no bounded progress");
		require(preview.prepwork <= 256, "preparation exceeded per-turn codepoint budget");
	}
	require(preview.prepared, "preparation did not publish a completed layout");
}

static void
check_atomic_yields(void)
{
	static const unsigned char delayed_utf8[] = "\xe4\xb8\xadX";
	static const unsigned char delayed_tab[] = "\tX";

	slow_metrics = 0;
	slow_budget_metrics = 1;
	begin(delayed_utf8, sizeof(delayed_utf8) - 1, 2000);
	previewprepare_step();
	require(preview.prepwork == 1 && preview.rawpos == 3 &&
	        preview.displayused == 3 &&
	        !memcmp(preview.display, "\xe4\xb8\xad", 3),
	        "budget yield split a UTF-8 scalar or lost its measured width");
	finish();
	begin(delayed_tab, sizeof(delayed_tab) - 1, 2000);
	previewprepare_step();
	require(preview.prepwork == 2 && preview.rawpos == 1 &&
	        preview.tabspaces == 3 && preview.displayused == 1 &&
	        preview.display[0] == ' ',
	        "budget yield did not retain resumable tab expansion state");
	finish();
	require(preview.displayused == 5 && !memcmp(preview.display, "    X", 5),
	        "resumed tab expansion changed text or column state");
	slow_budget_metrics = 0;
}

static void
check_rows(const size_t *ends, size_t count)
{
	size_t i;

	require(preview.rowcount == count, "row count differs");
	for (i = 0; i < count; i++) {
		require(preview.rows[i] == ends[i], "row end offset differs");
		require(preview.rows[i] <= preview.displayused, "row end exceeds display bytes");
		if (i)
			require(preview.rows[i] >= preview.rows[i - 1], "row ends are not ordered");
	}
}
static void
check_text(const unsigned char *raw, size_t rawlen, const char *expected,
           size_t expectedlen, size_t rows, unsigned int width)
{
	begin(raw, rawlen, width);
	finish();
	require(preview.displayused == expectedlen, "sanitized display length differs");
	require(!memcmp(preview.display, expected, expectedlen), "sanitized bytes differ");
	require(preview.rowcount == rows, "wrapped row count differs");
}
static void
check_all_bytes(void)
{
	unsigned char raw[256], expected[1025];
	size_t ends[257], used = 0, rows = 1, column = 0, i, spaces;

	for (i = 0; i < sizeof(raw); i++)
		raw[i] = (unsigned char)i;
	for (i = 0; i < sizeof(raw); i++) {
		if (raw[i] == '\n') {
			ends[rows - 1] = used;
			expected[used++] = '\0';
			rows++;
			column = 0;
		} else if (raw[i] == '\t') {
			spaces = 4 - column % 4;
			while (spaces--) {
				expected[used++] = ' ';
				column++;
			}
		} else if (!raw[i] || raw[i] == '\r' || raw[i] == 0x1b ||
		           raw[i] < 0x20 || raw[i] >= 0x7f) {
			memcpy(expected + used, REPLACEMENT, 3);
			used += 3;
			column++;
		} else {
			expected[used++] = raw[i];
			column++;
		}
	}
	ends[rows - 1] = used;
	check_text(raw, sizeof(raw), (const char *)expected, used, rows, 2000);
	check_rows(ends, rows);
}

static int
test_helper(void)
{
	unsigned char buf[4098];
	size_t sent;
	ssize_t n;

	memset(buf, 'x', 4095);
	memcpy(buf + 4095, "\xe4\xb8\xad", 3);
	sent = 0;
	while (sent < 4096) {
		n = write(STDOUT_FILENO, buf + sent, 4096 - sent);
		if (n > 0)
			sent += (size_t)n;
		else if (n != -1 || errno != EINTR)
			return 1;
	}
	sent = 4096;
	while (sent < sizeof(buf)) {
		n = write(STDOUT_FILENO, buf + sent, sizeof(buf) - sent);
		if (n > 0)
			sent += (size_t)n;
		else if (n != -1 || errno != EINTR)
			return 1;
	}
	return 0;
}

static void
check_helper_deadline(void)
{
	char path[PATH_MAX], label[] = "slowprep";
	struct item item = {0};
	struct pollfd fds[2];
	const char *helper = getenv("PREVIEW_TEXT_HELPER");
	int64_t launched;
	unsigned int count;
	int saw_split = 0;

	/* Match production setup when a fast helper closes its input early. */
	signal(SIGPIPE, SIG_IGN);
	require(helper != NULL && realpath(helper, path) != NULL,
	        "PREVIEW_TEXT_HELPER must name the normal helper executable");
	require(setenv("PREVIEW_TEXT_HELPER_CHILD", "1", 1) == 0,
	        "cannot configure synthetic helper child");
	previewcmd = path;
	free(preview.buf);
	testraw = NULL;
	preview.buf = malloc(PREVIEWMAX + 1);
	require(preview.buf != NULL, "cannot allocate production collection buffer");
	preview.used = 0;
	item.text = label;
	preview.item = &item;
	preview.generation = 1;
	preview.state = PreviewLoading;
	preview.due = 0;
	previewstart();
	launched = preview.deadline - 1000;
	require(preview.pid > 0 && preview.deadline > 0, "production helper did not start");
	while (preview.deadline) {
		count = 0;
		if (preview.input >= 0)
			fds[count++] = (struct pollfd){ preview.input, POLLOUT, 0 };
		if (preview.output >= 0)
			fds[count++] = (struct pollfd){ preview.output, POLLIN, 0 };
		if (poll(fds, count, 10) < 0 && errno != EINTR)
			require(0, "poll failed during native helper collection");
		if (preview.input >= 0)
			previewwrite();
		if (preview.output >= 0) {
			previewread();
			if (preview.used == 4096 && (unsigned char)preview.buf[4095] == 0xe4)
				saw_split = 1;
		}
		previewstep();
		require(preview.state != PreviewTimeout && preview.state != PreviewUnavailable,
		        "production helper collection failed");
	}
	require(preview.state == PreviewReady && preview.used == 4098 && saw_split,
	        "production helper did not collect valid UTF-8 split across 4096-byte reads");
	slow_metrics = 1;
	previewprepare_start(10000);
	while (previewprepare_pending()) {
		previewstep();
		previewprepare_step();
		require(preview.prepwork > 0 && preview.prepwork <= 256,
		        "delayed production prep step violated its work bound");
	}
	require(preview.displayused == 4098 &&
	        !memcmp(preview.display + 4095, "\xe4\xb8\xad", 3),
	        "split-read UTF-8 codepoint did not survive preparation");
	slow_metrics = 0;
	require(previewtime() - launched > 1000 && preview.state == PreviewReady,
	        "completed helper timed out during preparation beyond its deadline");
	printf("PASS completed helper: valid UTF-8 split at byte 4096, layout ready at %lld ms, budget <=256\n",
	       (long long)(previewtime() - launched));
	previewprepare_reset();
	free(preview.buf);
	preview.buf = NULL;
	preview.item = NULL;
	previewcmd = NULL;
	signal(SIGPIPE, SIG_DFL);
}

int
main(int argc, char **argv)
{
	if (getenv("PREVIEW_TEXT_HELPER_CHILD"))
		return test_helper();
	if (argc > 1 && !strcmp(argv[1], "--profile")) {
		const char *path = getenv("PREVIEW_PROFILE");

		require(path && (profilelog = fopen(path, "w")) != NULL,
		        "PREVIEW_PROFILE must name a writable profile log");
		profile_metrics = 1;
		profile_target = (void *)(uintptr_t)&preview_test_prepare_step_real;
		memmove(argv + 1, argv + 2, (size_t)(argc - 1) * sizeof(*argv));
		return dmenu_application_main(argc - 1, argv);
	}
	if (argc > 1 && !strcmp(argv[1], "--menu")) {
		slow_metrics = 2;
		memmove(argv + 1, argv + 2, (size_t)(argc - 1) * sizeof(*argv));
		return dmenu_application_main(argc - 1, argv);
	}
	if (argc > 1 && !strcmp(argv[1], "--atomic-yields")) {
		drw = &testdrw;
		check_atomic_yields();
		puts("PASS atomic UTF-8 and tab-space yields");
		return 0;
	}
	static const unsigned char multiline[] = "A\n\nB\n";
	static const unsigned char unicode[] = "caf\xc3\xa9 \xd0\x9a\xd0\xb8\xd1\x80\xd0\xb8\xd0\xbb\xd0\xbb\xd0\xb8\xd1\x86\xd0\xb0 \xce\x91\xce\xbb\xce\xbb\xce\xb7\xce\xbd\xce\xb9\xce\xba\xce\xac \xe4\xb8\xad\xf0\x9f\x98\x80 e\xcc\x81";
	static const unsigned char malformed[] = {
		0xff, ' ', 0xc3, 0x28, ' ', 0xe2, 0x82, ' ',
		0xed, 0xa0, 0x80, ' ', 0xc0, 0xaf, ' ', 0xf4, 0x90, 0x80, 0x80
	};
	static const unsigned char mixed[] = "abWWWc";
	static const unsigned char tab_after_wrap[] = "abc\t";
	static const unsigned char controls[] = { 'a', '\t', 'b', '\0', '\r', 0x1b, 1, 0x7f, 0xc2, 0x80, '\n' };
	static const unsigned char crlf[] = { 'x', '\r', '\n' };
	static const unsigned char ansi[] = "\x1b[31mRED\x1b[0m";
	static const unsigned char one_glyph[] = { 'W' };
	static unsigned char zero_width[1024];
	static unsigned char tabs[400], capped[PREVIEWMAX];
	static unsigned char longline[1024];
	static const char malformed_expected[] =
		"\xef\xbf\xbd \xef\xbf\xbd( "
		"\xef\xbf\xbd\xef\xbf\xbd "
		"\xef\xbf\xbd\xef\xbf\xbd\xef\xbf\xbd "
		"\xef\xbf\xbd\xef\xbf\xbd "
		"\xef\xbf\xbd\xef\xbf\xbd\xef\xbf\xbd\xef\xbf\xbd";
	size_t i;

	drw = &testdrw;
	check_text(multiline, sizeof(multiline) - 1, "A\0\0B\0", 5, 4, 80);
	{
		static const size_t ends[] = { 1, 2, 4, 5 };
		check_rows(ends, sizeof(ends) / sizeof(ends[0]));
	}
	check_text(unicode, sizeof(unicode) - 1, (const char *)unicode,
	           sizeof(unicode) - 1, 1, 2000);
	check_text(malformed, sizeof(malformed), malformed_expected,
	           sizeof(malformed_expected) - 1, 1, 2000);
	check_text(controls, sizeof(controls),
	           "a   b" REPLACEMENT REPLACEMENT REPLACEMENT REPLACEMENT REPLACEMENT REPLACEMENT "\0",
	           24, 2, 2000);
	check_text(ansi, sizeof(ansi) - 1,
	           REPLACEMENT "[31mRED" REPLACEMENT "[0m", 16, 1, 2000);
	check_all_bytes();
	check_text(crlf, sizeof(crlf), "x" REPLACEMENT "\0", 5, 2, 2000);
	check_text(mixed, sizeof(mixed) - 1, (const char *)mixed,
	           sizeof(mixed) - 1, 4, 5);
	{
		static const size_t ends[] = { 2, 3, 4, 6 };
		check_rows(ends, sizeof(ends) / sizeof(ends[0]));
	}
	check_text(tab_after_wrap, sizeof(tab_after_wrap) - 1, "abc ",
	           4, 2, 2);
	{
		static const size_t ends[] = { 2, 4 };
		check_rows(ends, sizeof(ends) / sizeof(ends[0]));
	}
	begin(unicode, sizeof(unicode) - 1, 3);
	finish();
	require(preview.rowcount > 1, "narrow Unicode width did not wrap");
	memset(zero_width, 'z', sizeof(zero_width));
	begin(zero_width, sizeof(zero_width), 1);
	finish();
	require(preview.displayused == sizeof(zero_width) && preview.rowcount == 1,
	        "zero-width glyphs did not make bounded preparation progress");
	check_text((const unsigned char *)"", 0, "", 0, 1, 0);
	check_text(one_glyph, sizeof(one_glyph), "W", 1, 1, 0);

	check_atomic_yields();

	/* Expanded spaces count toward the per-turn budget and retain tab stops across wraps. */
	memset(tabs, '\t', sizeof(tabs));
	begin(tabs, sizeof(tabs), 2000);
	for (i = 0; previewprepare_pending(); i++) {
		previewprepare_step();
		require(preview.prepwork <= 256, "tab expansion exceeded per-turn budget");
		require(i < 10, "expanded tabs did not finish in bounded turns");
	}
	require(preview.displayused == sizeof(tabs) * 4 && preview.rowcount == 1,
	        "tab expansion length or row count differs");
	for (i = 0; i < preview.displayused; i++)
		require(preview.display[i] == ' ', "tab expansion produced a non-space byte");

	/* A UTF-8 codepoint split at the exact byte cap is replaced bytewise. */
	memset(capped, 'x', sizeof(capped));
	capped[PREVIEWMAX - 3] = 0xf0;
	capped[PREVIEWMAX - 2] = 0x9f;
	capped[PREVIEWMAX - 1] = 0x98;
	begin(capped, sizeof(capped), 2000);
	finish();
	require(preview.displayused == PREVIEWMAX + 6,
	        "cap-split sequence did not produce three replacement codepoints");

	/* Worst-case tab expansion and trailing LF rows use the full bounded allocations. */
	memset(capped, '\t', sizeof(capped));
	begin(capped, sizeof(capped), 1);
	finish();
	require(preview.displayused == PREVIEWMAX * 4 &&
	        preview.rowcount == PREVIEWMAX * 4 &&
	        preview.rows[preview.rowcount - 1] == preview.displayused &&
	        preview.display[preview.displayused] == '\0',
	        "maximum tab expansion exceeded display/row bounds");
	memset(capped, '\n', sizeof(capped));
	begin(capped, sizeof(capped), 1);
	finish();
	require(preview.displayused == PREVIEWMAX && preview.rowcount == PREVIEWMAX + 1 &&
	        preview.rows[PREVIEWMAX] == PREVIEWMAX,
	        "maximum LF input lost its trailing empty row");

	memset(longline, 'W', sizeof(longline));
	begin(longline, sizeof(longline), 5);
	finish();
	require(preview.rowcount == sizeof(longline), "oversized glyph wrapping failed to make progress");
	for (i = 0; i < preview.rowcount; i++)
		require(preview.rows[i] == i + 1, "oversized glyph row offsets differ");

	begin(tabs, sizeof(tabs), 2000);
	require(previewprepare_pending(), "large preparation was not pending");
	previewprepare_reset();
	require(!previewprepare_pending() && !preview.display && !preview.rows,
	        "selection cancellation retained pending layout");

	/* Completed collection clears its deadline before bounded layout work begins. */
	begin(tabs, sizeof(tabs), 2000);
	preview.deadline = 0;
	previewstep();
	require(preview.state == PreviewReady && previewprepare_pending() &&
	        preview.prepwork <= 256,
	        "expired collection deadline incorrectly timed out pending layout");
	finish();
	require(preview.state == PreviewReady, "layout completion changed successful result state");
	previewprepare_reset();
	check_helper_deadline();
	previewprepare_reset();
	puts("PASS preview text preparation and late-layout lifecycle");
	free(testraw);
	return 0;
}
