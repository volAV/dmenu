/* Real-Xft regression probe for preview label suffix rendering. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/Xft/Xft.h>

#define WIDTH 256
#define HEIGHT 48
#define ITEM_X 8
#define ITEM_Y 8

#define main dmenu_application_main
#include "../dmenu.c"
#undef main

static void
need(int ok, const char *message)
{
	if (!ok) {
		fprintf(stderr, "FAIL preview label: %s\n", message);
		exit(1);
	}
}

static XImage *
read_image(void)
{
	XImage *image = XGetImage(dpy, drw->drawable, 0, 0, WIDTH, HEIGHT,
	                          AllPlanes, ZPixmap);
	need(image != NULL, "cannot read drawable pixels");
	return image;
}

static unsigned int
mismatch(XImage *a, XImage *b, int x, int y, int width, int height)
{
	unsigned int count = 0;
	int px, py;

	for (py = y; py < y + height; py++)
		for (px = x; px < x + width; px++)
			count += XGetPixel(a, px, py) != XGetPixel(b, px, py);
	return count;
}

static void
clear(void)
{
	XSetForeground(dpy, drw->gc, BlackPixel(dpy, screen));
	XFillRectangle(dpy, drw->drawable, drw->gc, 0, 0, WIDTH, HEIGHT);
}

static XImage *
render(struct item *item, int width, int with_preview, int selected, int out)
{
	clear();
	previewcmd = with_preview ? (char *)"/synthetic-preview" : NULL;
	sel = selected ? item : NULL;
	item->out = out;
	drawitem(item, ITEM_X, ITEM_Y, width);
	XSync(dpy, False);
	return read_image();
}

static void
check_ordinary(struct item *item, int width)
{
	XImage *ordinary = render(item, width, 0, 0, 0);
	XImage *previewed = render(item, width, 1, 0, 0);
	need(mismatch(ordinary, previewed, 0, 0, WIDTH, HEIGHT) == 0,
	     "short or malformed label changed ordinary rendering");
	XDestroyImage(ordinary);
	XDestroyImage(previewed);
}

static void
check_suffix(const char *text, int width, int selected, int out)
{
	const char *start = strrchr(text, '(') - 1;
	struct item item = { .text = (char *)text };
	char *original = strdup(text);
	XImage *actual, *expected;
	XftDraw *draw;
	XRectangle clip;
	int pad = lrpad / 2, available = MAX(0, width - 2 * pad);
	unsigned int suffixw, prefixw, dotsw;
	int baseline;

	need(original != NULL, "cannot save original label");
	actual = render(&item, width, 1, selected, out);
	suffixw = drw_fontset_getwidth_clamp(drw, start, available);
	prefixw = available - suffixw;
	clear();
	drw_setscheme(drw, selected ? scheme[SchemeSel] :
	                   out ? scheme[SchemeOut] : scheme[SchemeNorm]);
	XSetForeground(dpy, drw->gc, drw->scheme[ColBg].pixel);
	XFillRectangle(dpy, drw->drawable, drw->gc, ITEM_X, ITEM_Y, width, bh);
	if (suffixw) {
		draw = XftDrawCreate(dpy, drw->drawable, DefaultVisual(dpy, screen),
		                     DefaultColormap(dpy, screen));
		need(draw != NULL, "cannot create independent Xft reference");
		clip.x = ITEM_X + pad + prefixw;
		clip.y = ITEM_Y;
		clip.width = suffixw;
		clip.height = bh;
		XftDrawSetClipRectangles(draw, 0, 0, &clip, 1);
		baseline = ITEM_Y + ((int)bh - (int)drw->fonts->h) / 2 + drw->fonts->xfont->ascent;
		XftDrawStringUtf8(draw, &drw->scheme[ColFg], drw->fonts->xfont,
		                  clip.x, baseline, (const XftChar8 *)start,
		                  strlen(start));
		XftDrawDestroy(draw);
		if (prefixw) {
			dotsw = drw_fontset_getwidth_clamp(drw, "...", prefixw);
			draw = XftDrawCreate(dpy, drw->drawable, DefaultVisual(dpy, screen),
			                     DefaultColormap(dpy, screen));
			clip.x = ITEM_X + pad + prefixw - dotsw;
			clip.width = dotsw;
			XftDrawSetClipRectangles(draw, 0, 0, &clip, 1);
			XftDrawStringUtf8(draw, &drw->scheme[ColFg], drw->fonts->xfont,
			                  clip.x, baseline, (const XftChar8 *)"...", 3);
			XftDrawDestroy(draw);
		}
	}
	XSync(dpy, False);
	expected = read_image();
	need(mismatch(actual, expected, ITEM_X + pad + prefixw, ITEM_Y,
	              suffixw ? suffixw : 1, bh) == 0,
	     "terminal suffix is not independently right-aligned and clipped");
	if (prefixw) {
		dotsw = drw_fontset_getwidth_clamp(drw, "...", prefixw);
		need(!mismatch(actual, expected, ITEM_X + pad + prefixw - dotsw,
		               ITEM_Y, dotsw, bh), "prefix ellipsis pixels differ");
	}
	need(mismatch(actual, expected, 0, 0, ITEM_X, HEIGHT) == 0 &&
	     mismatch(actual, expected, ITEM_X + width, 0,
	              WIDTH - ITEM_X - width, HEIGHT) == 0 &&
	     mismatch(actual, expected, 0, 0, WIDTH, ITEM_Y) == 0 &&
	     mismatch(actual, expected, 0, ITEM_Y + bh,
	              WIDTH, HEIGHT - ITEM_Y - bh) == 0,
	     "drawing escaped item bounds");
	need(!strcmp(item.text, original), "drawitem modified original label");
	free(original);
	XDestroyImage(actual);
	XDestroyImage(expected);
}

int
main(void)
{
	const char *fonts[] = { "monospace:size=10" };
	const char *normal[] = { "#ffffff", "#101820" };
	const char *selected[] = { "#ffd700", "#202840" };
	const char *outline[] = { "#00ff80", "#401020" };
	struct item item = { .text = "short (2 lines)" };
	Display *display;
	int width, color;

	display = XOpenDisplay(NULL);
	need(display != NULL, "requires explicitly owned X display");
	dpy = display;
	screen = DefaultScreen(display);
	drw = drw_create(display, screen, RootWindow(display, screen), WIDTH, HEIGHT);
	need(drw != NULL, "cannot create drawing context");
	need(drw_fontset_create(drw, fonts, LENGTH(fonts)) != NULL,
	     "cannot load test font");
	lrpad = drw->fonts->h;
	bh = lrpad + 4;
	scheme[SchemeNorm] = drw_scm_create(drw, normal, LENGTH(normal));
	scheme[SchemeSel] = drw_scm_create(drw, selected, LENGTH(selected));
	scheme[SchemeOut] = drw_scm_create(drw, outline, LENGTH(outline));
	need(scheme[SchemeNorm] && scheme[SchemeSel] && scheme[SchemeOut],
	     "cannot allocate test colors");

	item.text = "short (2 lines)";
	check_ordinary(&item, WIDTH - ITEM_X * 2);
	item.text = "short label";
	check_ordinary(&item, WIDTH - ITEM_X * 2);
	item.text = "very long malformed (02 lines)";
	check_ordinary(&item, 48);
	item.text = "prefix (1 lines)";
	check_ordinary(&item, 48);
	item.text = "(2 lines)";
	check_ordinary(&item, 48);

	for (color = 0; color < 3; color++)
		for (width = 200; width >= 1; width--)
			check_suffix("A long first line with enough content to overflow ordinary label width (987 lines)",
			             width, color == 1, color == 2);
	for (width = 1; width <= 64; width++)
		check_suffix(" (987 lines)", width, 0, 0);
	for (color = 0; color < SchemeLast; color++)
		drw_scm_free(drw, scheme[color], 2);
	drw_free(drw);
	XCloseDisplay(dpy);

	puts("PASS preview label real-Xft pixel oracle");
	return 0;
}
