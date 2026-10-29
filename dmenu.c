/* See LICENSE file for copyright and license details. */
#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <fcntl.h>
#include <locale.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <sys/wait.h>
#include <unistd.h>

#include <X11/Xlib.h>
#include <X11/Xatom.h>
#include <X11/Xutil.h>
#ifdef XINERAMA
#include <X11/extensions/Xinerama.h>
#endif
#include <X11/Xft/Xft.h>

#include "drw.h"
#include "util.h"

/* macros */
#define INTERSECT(x,y,w,h,r)  (MAX(0, MIN((x)+(w),(r).x_org+(r).width)  - MAX((x),(r).x_org)) \
                             * MAX(0, MIN((y)+(h),(r).y_org+(r).height) - MAX((y),(r).y_org)))
#define TEXTW(X)              (drw_fontset_getwidth(drw, (X)) + lrpad)
#define PREVIEWMAX           65536

/* enums */
enum { SchemeNorm, SchemeSel, SchemeOut, SchemeLast }; /* color schemes */

struct item {
	char *text;
	struct item *left, *right;
	int out;
	double distance;
};

static char text[BUFSIZ] = "";
static char *embed, *previewcmd;
static int bh, mw, mh;
static int inputw = 0, promptw, itemw;
static int lrpad; /* sum of left and right padding */
static size_t cursor;
static struct item *items = NULL;
static struct item *matches, *matchend;
static struct item *prev, *curr, *next, *sel;
static int mon = -1, screen;

static Atom clip, utf8;
static Display *dpy;
static Window root, parentwin, win;
static XIC xic;

static Drw *drw;
static Clr *scheme[SchemeLast];

#include "config.h"

static int (*fstrncmp)(const char *, const char *, size_t) = strncmp;
static char *(*fstrstr)(const char *, const char *) = strstr;

enum { PreviewIdle, PreviewLoading, PreviewReady, PreviewEmpty,
       PreviewUnavailable, PreviewTimeout, PreviewTruncated };
static struct {
	struct item *item;
	unsigned long generation, running;
	pid_t pid, pgid;
	int input, output, state;
	int64_t due, deadline;
	size_t sent, inputlen, used;
	char *buf;
} preview = { .input = -1, .output = -1 };

static void drawmenu(void);

static int64_t
previewtime(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_MONOTONIC, &ts) < 0)
		die("clock_gettime:");
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void
previewclose(int *fd)
{
	if (*fd >= 0)
		close(*fd);
	*fd = -1;
}

static void
previewreap(void)
{
	pid_t pid;

	if (!preview.pid)
		return;
	pid = waitpid(preview.pid, NULL, WNOHANG);
	if (pid == preview.pid || (pid < 0 && errno == ECHILD))
		preview.pid = 0;
}

static void
previewstop(void)
{
	previewclose(&preview.input);
	previewclose(&preview.output);
	/* Signal before reaping: the unreaped leader pins our process-group ID. */
	if (preview.pgid)
		kill(-preview.pgid, SIGKILL);
	else if (preview.pid)
		kill(preview.pid, SIGKILL);
	preview.pgid = 0;
	preview.deadline = 0;
	previewreap();
}

static void
previewfail(int state)
{
	preview.state = state;
	preview.used = 0;
	preview.buf[0] = '\0';
	previewstop();
}

static void
previewselect(void)
{
	if (preview.item == sel)
		return;
	previewstop();
	preview.item = sel;
	preview.generation++;
	preview.used = 0;
	preview.buf[0] = '\0';
	preview.state = sel ? PreviewLoading : PreviewIdle;
	preview.due = sel ? previewtime() + 40 : 0;
}

static int
previewfd(int fd)
{
	int copy;

	if (fd < 0)
		return -1;
	if (fd < 3) {
		copy = fcntl(fd, F_DUPFD_CLOEXEC, 3);
		close(fd);
		return copy;
	}
	if (fcntl(fd, F_SETFD, FD_CLOEXEC) < 0) {
		close(fd);
		return -1;
	}
	return fd;
}

static void
previewstart(void)
{
	int in[2] = { -1, -1 }, out[2] = { -1, -1 }, nullfd = -1;
	char *const argv[] = { previewcmd, NULL };
	pid_t pid;

	preview.due = 0;
	preview.deadline = previewtime() + 1000;
	if (pipe(in) < 0 || pipe(out) < 0)
		goto fail;
	in[0] = previewfd(in[0]);
	in[1] = previewfd(in[1]);
	out[0] = previewfd(out[0]);
	out[1] = previewfd(out[1]);
	nullfd = previewfd(open("/dev/null", O_RDWR | O_CLOEXEC));
	if (in[0] < 0 || in[1] < 0 || out[0] < 0 || out[1] < 0 || nullfd < 0 ||
	    fcntl(in[1], F_SETFL, O_NONBLOCK) < 0 ||
	    fcntl(out[0], F_SETFL, O_NONBLOCK) < 0)
		goto fail;
	if ((pid = fork()) < 0)
		goto fail;
	if (!pid) {
		if (setpgid(0, 0) < 0 || dup2(in[0], STDIN_FILENO) < 0 ||
		    dup2(out[1], STDOUT_FILENO) < 0 || dup2(nullfd, STDERR_FILENO) < 0)
			_exit(127);
		closefrom(3);
		signal(SIGPIPE, SIG_DFL);
		signal(SIGCHLD, SIG_DFL);
		execv(previewcmd, argv);
		_exit(127);
	}
	preview.pid = pid;
	if (setpgid(pid, pid) == 0 || errno == EACCES)
		preview.pgid = pid;
	else {
		previewfail(PreviewUnavailable);
		goto fail;
	}
	preview.input = in[1];
	preview.output = out[0];
	preview.sent = 0;
	preview.inputlen = strlen(preview.item->text);
	preview.running = preview.generation;
	previewclose(&in[0]);
	previewclose(&out[1]);
	previewclose(&nullfd);
	return;

fail:
	previewclose(&in[0]);
	previewclose(&in[1]);
	previewclose(&out[0]);
	previewclose(&out[1]);
	previewclose(&nullfd);
	previewfail(PreviewUnavailable);
}

static void
previewwrite(void)
{
	const char *data;
	size_t n;
	ssize_t written;

	n = preview.inputlen - preview.sent;
	if (preview.sent == preview.inputlen) {
		data = "\n";
		n = 1;
	} else {
		data = preview.item->text + preview.sent;
		n = MIN(n, 4096);
	}
	written = write(preview.input, data, n);
	if (written > 0) {
		preview.sent += written;
		if (preview.sent > preview.inputlen)
			previewclose(&preview.input);
	} else if (written < 0 && errno == EPIPE) {
		/* A helper may finish without consuming its complete input. */
		previewclose(&preview.input);
	} else if (!written || (errno != EINTR && errno != EAGAIN)) {
		previewfail(PreviewUnavailable);
	}
}

static void
previewread(void)
{
	char extra;
	ssize_t n;

	if (preview.used == PREVIEWMAX)
		n = read(preview.output, &extra, 1);
	else
		n = read(preview.output, preview.buf + preview.used,
		         MIN(PREVIEWMAX - preview.used, 4096));
	if (n > 0) {
		if (preview.used == PREVIEWMAX) {
			preview.state = PreviewTruncated;
			previewstop();
		} else {
			preview.used += n;
			preview.buf[preview.used] = '\0';
		}
	} else if (!n) {
		previewclose(&preview.output);
	} else if (errno != EINTR && errno != EAGAIN) {
		previewfail(PreviewUnavailable);
	}
}

static void
previewstep(void)
{
	siginfo_t info = {0};

	if (preview.pid && !preview.deadline)
		previewreap();
	if (!preview.pid && preview.due && previewtime() >= preview.due)
		previewstart();
	if (!preview.deadline)
		return;
	if (previewtime() >= preview.deadline) {
		previewfail(PreviewTimeout);
		return;
	}
	/* Observe exit without releasing the PID until group teardown is sent. */
	if (waitid(P_PID, preview.pid, &info, WEXITED | WNOHANG | WNOWAIT) < 0) {
		if (errno != EINTR)
			previewfail(PreviewUnavailable);
		return;
	}
	if (info.si_pid && preview.input < 0 && preview.output < 0) {
		if (preview.running != preview.generation) {
			previewstop();
		} else if (info.si_code != CLD_EXITED || info.si_status != 0) {
			previewfail(PreviewUnavailable);
		} else {
			preview.state = preview.used ? PreviewReady : PreviewEmpty;
			previewstop();
		}
	}
}

static void
previewwait(void)
{
	struct pollfd fds[3];
	int state, timeout = -1, result;
	int64_t now;

	state = preview.state;
	previewstep();
	if (state != preview.state)
		drawmenu();
	now = previewtime();
	if (preview.pid)
		timeout = 20;
	if (preview.deadline)
		timeout = MIN(timeout, MAX(0, preview.deadline - now));
	else if (!preview.pid && preview.due)
		timeout = MAX(0, preview.due - now);
	if (XPending(dpy))
		timeout = 0;
	fds[0] = (struct pollfd){ ConnectionNumber(dpy), POLLIN, 0 };
	fds[1] = (struct pollfd){ preview.input, POLLOUT, 0 };
	fds[2] = (struct pollfd){ preview.output, POLLIN, 0 };
	XFlush(dpy);
	result = poll(fds, LENGTH(fds), timeout);
	if (result < 0) {
		if (errno != EINTR) {
			previewfail(PreviewUnavailable);
			drawmenu();
		}
		return;
	}
	state = preview.state;
	if ((fds[1].revents | fds[2].revents) & POLLNVAL)
		previewfail(PreviewUnavailable);
	if (preview.input >= 0 && (fds[1].revents & (POLLOUT | POLLHUP | POLLERR)))
		previewwrite();
	if (preview.output >= 0 && (fds[2].revents & (POLLIN | POLLHUP | POLLERR)))
		previewread();
	previewstep();
	if (state != preview.state)
		drawmenu();
}

static unsigned int
textw_clamp(const char *str, unsigned int n)
{
	unsigned int w = drw_fontset_getwidth_clamp(drw, str, n) + lrpad;
	return MIN(w, n);
}

static void
appenditem(struct item *item, struct item **list, struct item **last)
{
	if (*last)
		(*last)->right = item;
	else
		*list = item;

	item->left = *last;
	item->right = NULL;
	*last = item;
}

static void
calcoffsets(void)
{
	int i, n;

	if (lines > 0)
		n = lines * bh;
	else
		n = mw - (promptw + inputw + TEXTW("<") + TEXTW(">"));
	/* calculate which items will begin the next page and previous page */
	for (i = 0, next = curr; next; next = next->right)
		if ((i += (lines > 0) ? bh : textw_clamp(next->text, n)) > n)
			break;
	for (i = 0, prev = curr; prev && prev->left; prev = prev->left)
		if ((i += (lines > 0) ? bh : textw_clamp(prev->left->text, n)) > n)
			break;
}

static void
cleanup(void)
{
	size_t i;

	XUngrabKeyboard(dpy, CurrentTime);
	if (previewcmd) {
		XFlush(dpy);
		previewstop();
		free(preview.buf);
	}
	for (i = 0; i < SchemeLast; i++)
		drw_scm_free(drw, scheme[i], 2);
	for (i = 0; items && items[i].text; ++i)
		free(items[i].text);
	free(items);
	drw_free(drw);
	XSync(dpy, False);
	XCloseDisplay(dpy);
}

static char *
cistrstr(const char *h, const char *n)
{
	size_t i;

	if (!n[0])
		return (char *)h;

	for (; *h; ++h) {
		for (i = 0; n[i] && tolower((unsigned char)n[i]) ==
		            tolower((unsigned char)h[i]); ++i)
			;
		if (n[i] == '\0')
			return (char *)h;
	}
	return NULL;
}

static int
drawitem(struct item *item, int x, int y, int w)
{
	if (item == sel)
		drw_setscheme(drw, scheme[SchemeSel]);
	else if (item->out)
		drw_setscheme(drw, scheme[SchemeOut]);
	else
		drw_setscheme(drw, scheme[SchemeNorm]);

	return drw_text(drw, x, y, w, bh, lrpad / 2, item->text, 0);
}

static void
drawpreview(void)
{
	static const char *states[] = {
		"", "Loading", "", "No preview", "Unavailable", "Timed out", "Truncated"
	};
	int x = itemw + 1;
	unsigned int row, count;
	size_t offset = 0, length;
	char *end, saved;

	drw_setscheme(drw, scheme[SchemeNorm]);
	drw_rect(drw, itemw, bh, 1, mh - bh, 1, 0);
	if (!sel || preview.item != sel)
		return;
	count = lines > 1 ? lines - 1 : (preview.state == PreviewReady);
	if (preview.state == PreviewReady || preview.state == PreviewTruncated)
		for (row = 0; row < count && offset < preview.used; row++) {
			end = memchr(preview.buf + offset, '\n', preview.used - offset);
			length = end ? (size_t)(end - preview.buf) : preview.used;
			saved = preview.buf[length];
			preview.buf[length] = '\0';
			drw_text(drw, x, (row + 1) * bh, mw - x, bh,
			         lrpad / 2, preview.buf + offset, 0);
			preview.buf[length] = saved;
			offset = length + 1;
		}
	if (preview.state != PreviewReady)
		drw_text(drw, x, lines * bh, mw - x, bh, lrpad / 2,
		         states[preview.state], 0);
}

static void
drawmenu(void)
{
	unsigned int curpos;
	struct item *item;
	int x = 0, y = 0, w;
	if (previewcmd)
		previewselect();

	drw_setscheme(drw, scheme[SchemeNorm]);
	drw_rect(drw, 0, 0, mw, mh, 1, 1);

	if (prompt && *prompt) {
		drw_setscheme(drw, scheme[SchemeSel]);
		x = drw_text(drw, x, 0, promptw, bh, lrpad / 2, prompt, 0);
	}
	/* draw input field */
	w = (lines > 0 || !matches) ? mw - x : inputw;
	drw_setscheme(drw, scheme[SchemeNorm]);
	drw_text(drw, x, 0, w, bh, lrpad / 2, text, 0);

	curpos = TEXTW(text) - TEXTW(&text[cursor]);
	if ((curpos += lrpad / 2 - 1) < w) {
		drw_setscheme(drw, scheme[SchemeNorm]);
		drw_rect(drw, x + curpos, 2, 2, bh - 4, 1, 0);
	}

	if (lines > 0) {
		/* draw vertical list */
		for (item = curr; item != next; item = item->right)
			drawitem(item, previewcmd ? 0 : x, y += bh,
			         previewcmd ? itemw : mw - x);
	} else if (matches && curr) {
		/* draw horizontal list */
		x += inputw;
		w = TEXTW("<");
		if (curr->left) {
			drw_setscheme(drw, scheme[SchemeNorm]);
			drw_text(drw, x, 0, w, bh, lrpad / 2, "<", 0);
		}
		x += w;
		for (item = curr; item != next; item = item->right)
			x = drawitem(item, x, 0, textw_clamp(item->text, mw - x - TEXTW(">")));
		if (next) {
			w = TEXTW(">");
			drw_setscheme(drw, scheme[SchemeNorm]);
			drw_text(drw, mw - w, 0, w, bh, lrpad / 2, ">", 0);
		}
	}
	if (previewcmd)
		drawpreview();
	drw_map(drw, win, 0, 0, mw, mh);
}

static void
grabfocus(void)
{
	struct timespec ts = { .tv_sec = 0, .tv_nsec = 10000000  };
	Window focuswin;
	int i, revertwin;

	for (i = 0; i < 100; ++i) {
		XGetInputFocus(dpy, &focuswin, &revertwin);
		if (focuswin == win)
			return;
		XSetInputFocus(dpy, win, RevertToParent, CurrentTime);
		nanosleep(&ts, NULL);
	}
	die("cannot grab focus");
}

static void
grabkeyboard(void)
{
	struct timespec ts = { .tv_sec = 0, .tv_nsec = 1000000  };
	int i;

	if (embed)
		return;
	/* try to grab keyboard, we may have to wait for another process to ungrab */
	for (i = 0; i < 1000; i++) {
		if (XGrabKeyboard(dpy, DefaultRootWindow(dpy), True, GrabModeAsync,
		                  GrabModeAsync, CurrentTime) == GrabSuccess)
			return;
		nanosleep(&ts, NULL);
	}
	die("cannot grab keyboard");
}

int
compare_distance(const void *a, const void *b)
{
	struct item *da = *(struct item **) a;
	struct item *db = *(struct item **) b;

	if (!db)
		return 1;
	if (!da)
		return -1;

	return da->distance == db->distance ? 0 : da->distance < db->distance ? -1 : 1;
}

void
fuzzymatch(void)
{
	/* bang - we have so much memory */
	struct item *it;
	struct item **fuzzymatches = NULL;
	char c;
	int number_of_matches = 0, i, pidx, sidx, eidx;
	int text_len = strlen(text), itext_len;

	matches = matchend = NULL;

	/* walk through all items */
	for (it = items; it && it->text; ++it) {
		if (text_len) {
			itext_len = strlen(it->text);
			pidx = 0; /* pointer */
			sidx = eidx = -1; /* start of match, end of match */
			/* walk through item text */
			for (i = 0; i < itext_len && (c = it->text[i]); ++i) {
				/* fuzzy match pattern */
				if (!fstrncmp(&text[pidx], &c, 1)) {
					if(sidx == -1)
						sidx = i;
					++pidx;
					if (pidx == text_len) {
						eidx = i;
						break;
					}
				}
			}
			/* build list of matches */
			if (eidx != -1) {
				/* compute distance */
				/* add penalty if match starts late (log(sidx+2))
				 * add penalty for long a match without many matching characters */
				it->distance = log(sidx + 2) + (double)(eidx - sidx - text_len);
				/* fprintf(stderr, "distance %s %f\n", it->text, it->distance); */
				appenditem(it, &matches, &matchend);
				++number_of_matches;
			}
		} else {
			appenditem(it, &matches, &matchend);
		}
	}

	if (number_of_matches) {
		/* initialize array with matches */
		if (!(fuzzymatches = realloc(fuzzymatches,
		                             number_of_matches * sizeof(struct item *))))
			die("cannot realloc %u bytes:", number_of_matches * sizeof(struct item *));
		for (i = 0, it = matches; it && i < number_of_matches; ++i, it = it->right)
			fuzzymatches[i] = it;
		/* sort matches according to distance */
		qsort(fuzzymatches, number_of_matches, sizeof(struct item*), compare_distance);
		/* rebuild list of matches */
		matches = matchend = NULL;
		for (i = 0; i < number_of_matches &&
		        (it = fuzzymatches[i]) && it->text; ++i)
			appenditem(it, &matches, &matchend);
		free(fuzzymatches);
	}
	curr = sel = matches;
	calcoffsets();
}

static void
match(void)
{
	if (fuzzy) {
		fuzzymatch();
		return;
	}
	static char **tokv = NULL;
	static int tokn = 0;

	char buf[sizeof text], *s;
	int i, tokc = 0;
	size_t len, textsize;
	struct item *item, *lprefix, *lsubstr, *prefixend, *substrend;

	strcpy(buf, text);
	/* separate input text into tokens to be matched individually */
	for (s = strtok(buf, " "); s; tokv[tokc - 1] = s, s = strtok(NULL, " "))
		if (++tokc > tokn && !(tokv = realloc(tokv, ++tokn * sizeof *tokv)))
			die("cannot realloc %zu bytes:", tokn * sizeof *tokv);
	len = tokc ? strlen(tokv[0]) : 0;

	matches = lprefix = lsubstr = matchend = prefixend = substrend = NULL;
	textsize = strlen(text) + 1;
	for (item = items; item && item->text; item++) {
		for (i = 0; i < tokc; i++)
			if (!fstrstr(item->text, tokv[i]))
				break;
		if (i != tokc) /* not all tokens match */
			continue;
		/* exact matches go first, then prefixes, then substrings */
		if (!tokc || !fstrncmp(text, item->text, textsize))
			appenditem(item, &matches, &matchend);
		else if (!fstrncmp(tokv[0], item->text, len))
			appenditem(item, &lprefix, &prefixend);
		else
			appenditem(item, &lsubstr, &substrend);
	}
	if (lprefix) {
		if (matches) {
			matchend->right = lprefix;
			lprefix->left = matchend;
		} else
			matches = lprefix;
		matchend = prefixend;
	}
	if (lsubstr) {
		if (matches) {
			matchend->right = lsubstr;
			lsubstr->left = matchend;
		} else
			matches = lsubstr;
		matchend = substrend;
	}
	curr = sel = matches;
	calcoffsets();
}

static void
insert(const char *str, ssize_t n)
{
	if (strlen(text) + n > sizeof text - 1)
		return;
	/* move existing text out of the way, insert new text, and update cursor */
	memmove(&text[cursor + n], &text[cursor], sizeof text - cursor - MAX(n, 0));
	if (n > 0)
		memcpy(&text[cursor], str, n);
	cursor += n;
	match();
}

static size_t
nextrune(int inc)
{
	ssize_t n;

	/* return location of next utf8 rune in the given direction (+1 or -1) */
	for (n = cursor + inc; n + inc >= 0 && (text[n] & 0xc0) == 0x80; n += inc)
		;
	return n;
}

static void
movewordedge(int dir)
{
	if (dir < 0) { /* move cursor to the start of the word*/
		while (cursor > 0 && strchr(worddelimiters, text[nextrune(-1)]))
			cursor = nextrune(-1);
		while (cursor > 0 && !strchr(worddelimiters, text[nextrune(-1)]))
			cursor = nextrune(-1);
	} else { /* move cursor to the end of the word */
		while (text[cursor] && strchr(worddelimiters, text[cursor]))
			cursor = nextrune(+1);
		while (text[cursor] && !strchr(worddelimiters, text[cursor]))
			cursor = nextrune(+1);
	}
}

static void
keypress(XKeyEvent *ev)
{
	char buf[64];
	int len;
	KeySym ksym = NoSymbol;
	Status status;

	len = XmbLookupString(xic, ev, buf, sizeof buf, &ksym, &status);
	switch (status) {
	default: /* XLookupNone, XBufferOverflow */
		return;
	case XLookupChars: /* composed string from input method */
		goto insert;
	case XLookupKeySym:
	case XLookupBoth: /* a KeySym and a string are returned: use keysym */
		break;
	}

	if (ev->state & ControlMask) {
		switch(ksym) {
		case XK_a: ksym = XK_Home;      break;
		case XK_b: ksym = XK_Left;      break;
		case XK_c: ksym = XK_Escape;    break;
		case XK_d: ksym = XK_Delete;    break;
		case XK_e: ksym = XK_End;       break;
		case XK_f: ksym = XK_Right;     break;
		case XK_g: ksym = XK_Escape;    break;
		case XK_h: ksym = XK_BackSpace; break;
		case XK_i: ksym = XK_Tab;       break;
		case XK_j: /* fallthrough */
		case XK_J: /* fallthrough */
		case XK_m: /* fallthrough */
		case XK_M: ksym = XK_Return; ev->state &= ~ControlMask; break;
		case XK_n: ksym = XK_Down;      break;
		case XK_p: ksym = XK_Up;        break;

		case XK_k: /* delete right */
			text[cursor] = '\0';
			match();
			break;
		case XK_u: /* delete left */
			insert(NULL, 0 - cursor);
			break;
		case XK_w: /* delete word */
			while (cursor > 0 && strchr(worddelimiters, text[nextrune(-1)]))
				insert(NULL, nextrune(-1) - cursor);
			while (cursor > 0 && !strchr(worddelimiters, text[nextrune(-1)]))
				insert(NULL, nextrune(-1) - cursor);
			break;
		case XK_y: /* paste selection */
		case XK_Y:
			XConvertSelection(dpy, (ev->state & ShiftMask) ? clip : XA_PRIMARY,
			                  utf8, utf8, win, CurrentTime);
			return;
		case XK_Left:
		case XK_KP_Left:
			movewordedge(-1);
			goto draw;
		case XK_Right:
		case XK_KP_Right:
			movewordedge(+1);
			goto draw;
		case XK_Return:
		case XK_KP_Enter:
			break;
		case XK_bracketleft:
			cleanup();
			exit(1);
		default:
			return;
		}
	} else if (ev->state & Mod1Mask) {
		switch(ksym) {
		case XK_b:
			movewordedge(-1);
			goto draw;
		case XK_f:
			movewordedge(+1);
			goto draw;
		case XK_g: ksym = XK_Home;  break;
		case XK_G: ksym = XK_End;   break;
		case XK_h: ksym = XK_Up;    break;
		case XK_j: ksym = XK_Next;  break;
		case XK_k: ksym = XK_Prior; break;
		case XK_l: ksym = XK_Down;  break;
		default:
			return;
		}
	}

	switch(ksym) {
	default:
insert:
		if (len > 0 && !iscntrl((unsigned char)*buf))
			insert(buf, len);
		break;
	case XK_Delete:
	case XK_KP_Delete:
		if (text[cursor] == '\0')
			return;
		cursor = nextrune(+1);
		/* fallthrough */
	case XK_BackSpace:
		if (cursor == 0)
			return;
		insert(NULL, nextrune(-1) - cursor);
		break;
	case XK_End:
	case XK_KP_End:
		if (text[cursor] != '\0') {
			cursor = strlen(text);
			break;
		}
		if (next) {
			/* jump to end of list and position items in reverse */
			curr = matchend;
			calcoffsets();
			curr = prev;
			calcoffsets();
			while (next && (curr = curr->right))
				calcoffsets();
		}
		sel = matchend;
		break;
	case XK_Escape:
		cleanup();
		exit(1);
	case XK_Home:
	case XK_KP_Home:
		if (sel == matches) {
			cursor = 0;
			break;
		}
		sel = curr = matches;
		calcoffsets();
		break;
	case XK_Left:
	case XK_KP_Left:
		if (cursor > 0 && (!sel || !sel->left || lines > 0)) {
			cursor = nextrune(-1);
			break;
		}
		if (lines > 0)
			return;
		/* fallthrough */
	case XK_Up:
	case XK_KP_Up:
		if (sel && sel->left && (sel = sel->left)->right == curr) {
			curr = prev;
			calcoffsets();
		}
		break;
	case XK_Next:
	case XK_KP_Next:
		if (!next)
			return;
		sel = curr = next;
		calcoffsets();
		break;
	case XK_Prior:
	case XK_KP_Prior:
		if (!prev)
			return;
		sel = curr = prev;
		calcoffsets();
		break;
	case XK_Return:
	case XK_KP_Enter:
		puts((sel && !(ev->state & ShiftMask)) ? sel->text : text);
		if (!(ev->state & ControlMask)) {
			cleanup();
			exit(0);
		}
		if (sel)
			sel->out = 1;
		break;
	case XK_Right:
	case XK_KP_Right:
		if (text[cursor] != '\0') {
			cursor = nextrune(+1);
			break;
		}
		if (lines > 0)
			return;
		/* fallthrough */
	case XK_Down:
	case XK_KP_Down:
		if (sel && sel->right && (sel = sel->right) == next) {
			curr = next;
			calcoffsets();
		}
		break;
	case XK_Tab:
		if (!sel)
			return;
		cursor = strnlen(sel->text, sizeof text - 1);
		memcpy(text, sel->text, cursor);
		text[cursor] = '\0';
		match();
		break;
	}

draw:
	drawmenu();
}

static void
paste(void)
{
	char *p, *q;
	int di;
	unsigned long dl;
	Atom da;

	/* we have been given the current selection, now insert it into input */
	if (XGetWindowProperty(dpy, win, utf8, 0, (sizeof text / 4) + 1, False,
	                   utf8, &da, &di, &dl, &dl, (unsigned char **)&p)
	    == Success && p) {
		insert(p, (q = strchr(p, '\n')) ? q - p : (ssize_t)strlen(p));
		XFree(p);
	}
	drawmenu();
}

static void
readstdin(void)
{
	char *line = NULL;
	size_t i, itemsiz = 0, linesiz = 0;
	ssize_t len;

	/* read each line from stdin and add it to the item list */
	for (i = 0; (len = getline(&line, &linesiz, stdin)) != -1; i++) {
		if (i + 1 >= itemsiz) {
			itemsiz += 256;
			if (!(items = realloc(items, itemsiz * sizeof(*items))))
				die("cannot realloc %zu bytes:", itemsiz * sizeof(*items));
		}
		if (line[len - 1] == '\n')
			line[len - 1] = '\0';
		if (!(items[i].text = strdup(line)))
			die("strdup:");

		items[i].out = 0;
	}
	free(line);
	if (items)
		items[i].text = NULL;
	if (!previewcmd)
		lines = MIN(lines, i);
}

static void
run(void)
{
	XEvent ev;
	unsigned int batch = 0;

	for (;;) {
		if (previewcmd && (batch == 64 || !XPending(dpy))) {
			previewwait();
			batch = 0;
			continue;
		}
		if (XNextEvent(dpy, &ev))
			break;
		if (previewcmd)
			batch++;
		if (XFilterEvent(&ev, win))
			continue;
		switch(ev.type) {
		case DestroyNotify:
			if (ev.xdestroywindow.window != win)
				break;
			cleanup();
			exit(1);
		case Expose:
			if (ev.xexpose.count == 0)
				drw_map(drw, win, 0, 0, mw, mh);
			break;
		case FocusIn:
			/* regrab focus from parent window */
			if (ev.xfocus.window != win)
				grabfocus();
			break;
		case KeyPress:
			keypress(&ev.xkey);
			break;
		case SelectionNotify:
			if (ev.xselection.property == utf8)
				paste();
			break;
		case VisibilityNotify:
			if (ev.xvisibility.state != VisibilityUnobscured)
				XRaiseWindow(dpy, win);
			break;
		}
	}
}

static void
setup(void)
{
	int x, y, i, j, height = 0, minw;
	unsigned int du;
	XSetWindowAttributes swa;
	XIM xim;
	Window w, dw, *dws;
	XWindowAttributes wa;
	XClassHint ch = {"dmenu", "dmenu"};
#ifdef XINERAMA
	XineramaScreenInfo *info;
	Window pw;
	int a, di, n, area = 0;
#endif
	/* init appearance */
	for (j = 0; j < SchemeLast; j++)
		scheme[j] = drw_scm_create(drw, colors[j], 2);

	clip = XInternAtom(dpy, "CLIPBOARD",   False);
	utf8 = XInternAtom(dpy, "UTF8_STRING", False);

	/* calculate menu geometry */
	bh = drw->fonts->h + 2;
	lines = MAX(lines, 0);
	mh = previewcmd ? 0 : (lines + 1) * bh;
#ifdef XINERAMA
	i = 0;
	if (parentwin == root && (info = XineramaQueryScreens(dpy, &n))) {
		if (n < 1)
			die("Xinerama reported no screens");
		XGetInputFocus(dpy, &w, &di);
		if (mon >= 0 && mon < n)
			i = mon;
		else if (w != root && w != PointerRoot && w != None) {
			/* find top-level window containing current input focus */
			do {
				if (XQueryTree(dpy, (pw = w), &dw, &w, &dws, &du) && dws)
					XFree(dws);
			} while (w != root && w != pw);
			/* find xinerama screen with which the window intersects most */
			if (XGetWindowAttributes(dpy, pw, &wa))
				for (j = 0; j < n; j++)
					if ((a = INTERSECT(wa.x, wa.y, wa.width, wa.height, info[j])) > area) {
						area = a;
						i = j;
					}
		}
		/* no focused window is on screen, so use pointer location instead */
		if (mon < 0 && !area && XQueryPointer(dpy, root, &dw, &dw, &x, &y, &di, &di, &du))
			for (i = 0; i < n; i++)
				if (INTERSECT(x, y, 1, 1, info[i]) != 0)
					break;
		/* fallback to the first screen if there is no intersection */
		if (i >= n)
			i = 0;

		x = info[i].x_org;
		height = info[i].height;
		y = info[i].y_org + (topbar ? 0 : info[i].height - mh);
		mw = info[i].width;
		XFree(info);
	} else
#endif
	{
		if (!XGetWindowAttributes(dpy, parentwin, &wa))
			die("could not get embedding window attributes: 0x%lx",
			    parentwin);
		x = 0;
		y = topbar ? 0 : wa.height - mh;
		mw = wa.width;
		height = wa.height;
	}
	promptw = (prompt && *prompt) ? TEXTW(prompt) - lrpad / 4 : 0;
	if (previewcmd) {
		minw = TEXTW("M");
		itemw = (mw - 1) * 2 / 5;
		if (height / bh < 2 || itemw < minw || mw - itemw - 1 < minw)
			die("preview geometry is too small");
		lines = MIN(lines, (unsigned int)(height / bh - 1));
		mh = (lines + 1) * bh;
		if (!topbar)
			y -= mh;
		promptw = MIN(promptw, mw - minw);
	}
	inputw = mw / 3; /* input width: ~33% of monitor width */
	match();

	/* create menu window */
	swa.override_redirect = True;
	swa.background_pixel = scheme[SchemeNorm][ColBg].pixel;
	swa.event_mask = ExposureMask | KeyPressMask | VisibilityChangeMask;
	if (previewcmd)
		swa.event_mask |= StructureNotifyMask;
	win = XCreateWindow(dpy, root, x, y, mw, mh, 0,
	                    CopyFromParent, CopyFromParent, CopyFromParent,
	                    CWOverrideRedirect | CWBackPixel | CWEventMask, &swa);
	XSetClassHint(dpy, win, &ch);

	/* input methods */
	if ((xim = XOpenIM(dpy, NULL, NULL, NULL)) == NULL)
		die("XOpenIM failed: could not open input device");

	if ((xic = XCreateIC(xim, XNInputStyle, XIMPreeditNothing | XIMStatusNothing,
	                XNClientWindow, win, XNFocusWindow, win, NULL)) == NULL)
		die("XCreateIC failed: could not obtain input context");

	XMapRaised(dpy, win);
	if (embed) {
		XReparentWindow(dpy, win, parentwin, x, y);
		XSelectInput(dpy, parentwin, FocusChangeMask | SubstructureNotifyMask);
		if (XQueryTree(dpy, parentwin, &dw, &w, &dws, &du) && dws) {
			for (i = 0; i < du && dws[i] != win; ++i)
				XSelectInput(dpy, dws[i], FocusChangeMask);
			XFree(dws);
		}
		grabfocus();
	}
	drw_resize(drw, mw, mh);
	drawmenu();
}

static void
usage(void)
{
	die("usage: dmenu [-bFfiv] [-l lines] [-p prompt] [-P /helper] [-fn font] [-m monitor]\n"
	    "             [-nb color] [-nf color] [-sb color] [-sf color] [-w windowid]\n"
	    "       -P requires an absolute executable path and a positive -l");
}

int
main(int argc, char *argv[])
{
	XWindowAttributes wa;
	int i, fast = 0;
	char *linearg = NULL, *end;
	unsigned long n;

	for (i = 1; i < argc; i++)
		/* these options take no arguments */
		if (!strcmp(argv[i], "-v")) {      /* prints version information */
			puts("dmenu-"VERSION);
			exit(0);
		} else if (!strcmp(argv[i], "-b")) /* appears at the bottom of the screen */
			topbar = 0;
		else if (!strcmp(argv[i], "-F"))   /* disables fuzzy matching */
			fuzzy = 0;
		else if (!strcmp(argv[i], "-f"))   /* grabs keyboard before reading stdin */
			fast = 1;
		else if (!strcmp(argv[i], "-i")) { /* case-insensitive item matching */
			fstrncmp = strncasecmp;
			fstrstr = cistrstr;
		} else if (i + 1 == argc)
			usage();
		/* these options take one argument */
		else if (!strcmp(argv[i], "-l"))   /* number of lines in vertical list */
			linearg = argv[++i];
		else if (!strcmp(argv[i], "-m"))
			mon = atoi(argv[++i]);
		else if (!strcmp(argv[i], "-p"))   /* adds prompt to left of input field */
			prompt = argv[++i];
		else if (!strcmp(argv[i], "-P"))   /* opt-in preview pane */
			previewcmd = argv[++i];
		else if (!strcmp(argv[i], "-fn"))  /* font or font set */
			fonts[0] = argv[++i];
		else if (!strcmp(argv[i], "-nb"))  /* normal background color */
			colors[SchemeNorm][ColBg] = argv[++i];
		else if (!strcmp(argv[i], "-nf"))  /* normal foreground color */
			colors[SchemeNorm][ColFg] = argv[++i];
		else if (!strcmp(argv[i], "-sb"))  /* selected background color */
			colors[SchemeSel][ColBg] = argv[++i];
		else if (!strcmp(argv[i], "-sf"))  /* selected foreground color */
			colors[SchemeSel][ColFg] = argv[++i];
		else if (!strcmp(argv[i], "-ob"))  /* outline background color */
			colors[SchemeOut][ColBg] = argv[++i];
		else if (!strcmp(argv[i], "-of"))  /* outline foreground color */
			colors[SchemeOut][ColFg] = argv[++i];
		else if (!strcmp(argv[i], "-w"))   /* embedding window id */
			embed = argv[++i];
		else
			usage();

	if (previewcmd) {
		if (previewcmd[0] != '/' || !linearg || !isdigit((unsigned char)linearg[0]))
			usage();
		errno = 0;
		n = strtoul(linearg, &end, 10);
		if (errno || end == linearg || *end || !n || n > UINT_MAX)
			usage();
		lines = n;
	} else if (linearg) {
		lines = atoi(linearg);
	}

	if (previewcmd) {
		preview.buf = ecalloc(PREVIEWMAX + 1, 1);
		signal(SIGPIPE, SIG_IGN);
		signal(SIGCHLD, SIG_DFL);
	}
	if (!setlocale(LC_CTYPE, "") || !XSupportsLocale())
		fputs("warning: no locale support\n", stderr);
	if (!(dpy = XOpenDisplay(NULL)))
		die("cannot open display");
	screen = DefaultScreen(dpy);
	root = RootWindow(dpy, screen);
	if (!embed || !(parentwin = strtol(embed, NULL, 0)))
		parentwin = root;
	if (!XGetWindowAttributes(dpy, parentwin, &wa))
		die("could not get embedding window attributes: 0x%lx",
		    parentwin);
	drw = drw_create(dpy, screen, root, wa.width, wa.height);
	if (!drw_fontset_create(drw, fonts, LENGTH(fonts)))
		die("no fonts could be loaded.");
	lrpad = drw->fonts->h;

#ifdef __OpenBSD__
	if (pledge(previewcmd ? "stdio rpath proc exec" : "stdio rpath", NULL) == -1)
		die("pledge");
#endif

	if (fast && !isatty(0)) {
		grabkeyboard();
		readstdin();
	} else {
		readstdin();
		grabkeyboard();
	}
	setup();
	run();

	return 1; /* unreachable */
}
