/* Real-Xft content oracle for preview clipping; run via preview-text.py. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/Xft/Xft.h>
#include "../drw.h"
#include "../util.h"

#define WIDTH 40
#define HEIGHT 16

static void
need(int ok, const char *message)
{
	if (!ok) {
		fprintf(stderr, "FAIL preview clipping: %s\n", message);
		exit(1);
	}
}

static XImage *
image(Drw *drw, const char *directory, const char *name)
{
	char path[4096];
	FILE *out;
	XImage *pixels = XGetImage(drw->dpy, drw->drawable, 0, 0, 80, 32, AllPlanes, ZPixmap);
	unsigned long pixel;
	int x, y;

	need(pixels != NULL, "cannot read drawable pixels");
	need(snprintf(path, sizeof path, "%s/%s.ppm", directory, name) < (int)sizeof path,
	     "output path too long");
	out = fopen(path, "wb");
	need(out != NULL, "cannot save screenshot");
	fputs("P6\n80 32\n255\n", out);
	for (y = 0; y < 32; y++)
		for (x = 0; x < 80; x++) {
			pixel = XGetPixel(pixels, x, y);
			/* The isolated regression display uses 24-bit TrueColor. */
			fputc((pixel >> 16) & 255, out);
			fputc((pixel >> 8) & 255, out);
			fputc(pixel & 255, out);
		}
	need(fclose(out) == 0, "cannot finish screenshot");
	return pixels;
}

static void
clear(Drw *drw)
{
	drw_rect(drw, 0, 0, 80, 32, 1, 1);
}

static void
reference(Drw *drw, Fnt *font, int x, const char *text, int clipped)
{
	XRectangle rect = { 0, 0, WIDTH, HEIGHT };
	XftDraw *draw = XftDrawCreate(drw->dpy, drw->drawable,
	                            DefaultVisual(drw->dpy, drw->screen),
	                            DefaultColormap(drw->dpy, drw->screen));
	int baseline = ((int)HEIGHT - (int)font->h) / 2 + font->xfont->ascent;

	need(draw != NULL, "cannot create reference drawing context");
	if (clipped)
		XftDrawSetClipRectangles(draw, 0, 0, &rect, 1);
	XftDrawStringUtf8(draw, &drw->scheme[ColFg], font->xfont, x, baseline,
	                  (const XftChar8 *)text, strlen(text));
	XftDrawDestroy(draw);
}

static unsigned int
mismatch(XImage *a, XImage *b, int width, int height)
{
	unsigned int count = 0;
	int x, y;

	for (y = 0; y < height; y++)
		for (x = 0; x < width; x++)
			count += XGetPixel(a, x, y) != XGetPixel(b, x, y);
	return count;
}

int
main(int argc, char **argv)
{
	const char *fonts[] = { "Liberation Mono:size=8", "DejaVu Sans:size=48" };
	const char *colors[] = { "#ffffff", "#000000" };
	const char *glyph = "\xf0\x9f\x98\x80";
	Display *display;
	Drw *drw;
	Fnt *fallback;
	XImage *expected, *actual, *ellipsis, *ordinary, *blank, *prefix, *prefix_actual;
	XImage *sentinel, *after_clip;
	unsigned int advance, prefix_width, same, wrong, empty, ordinary_diff, prefix_diff, leak;
	int i;

	need(argc == 2 && argv[1][0] == '/', "requires absolute screenshot directory");
	display = XOpenDisplay(NULL);
	need(display != NULL, "requires owned isolated X display");
	need(DefaultDepth(display, DefaultScreen(display)) == 24, "requires 24-bit test display");
	drw = drw_create(display, DefaultScreen(display), DefaultRootWindow(display), 80, 32);
	need(drw_fontset_create(drw, fonts, 2) != NULL && drw->fonts->next != NULL,
	     "both fixture fonts must load");
	fallback = drw->fonts->next;
	need(!XftCharExists(display, drw->fonts->xfont, 0x1f600) &&
	     XftCharExists(display, fallback->xfont, 0x1f600), "fixture must use the second font");
	drw_setscheme(drw, drw_scm_create(drw, colors, 2));
	drw_font_getexts(fallback, glyph, strlen(glyph), &advance, NULL);
	need(advance > WIDTH, "fixture glyph must be overwide");
	clear(drw);
	blank = image(drw, argv[1], "blank");
	reference(drw, fallback, 0, glyph, 1);
	expected = image(drw, argv[1], "expected-clipped-glyph");
	clear(drw);
	drw_text_clip(drw, 0, 0, WIDTH, HEIGHT, 0, glyph, 0);
	actual = image(drw, argv[1], "actual-clipped-glyph");
	clear(drw);
	reference(drw, drw->fonts, 0, "...", 0);
	ellipsis = image(drw, argv[1], "expected-ellipsis");
	clear(drw);
	drw_text(drw, 0, 0, WIDTH, HEIGHT, 0, glyph, 0);
	ordinary = image(drw, argv[1], "ordinary-overflow");
	clear(drw);
	reference(drw, drw->fonts, 0, "x", 1);
	drw_font_getexts(drw->fonts, "x", 1, &prefix_width, NULL);
	reference(drw, fallback, prefix_width, glyph, 1);
	prefix = image(drw, argv[1], "expected-prefix-glyph");
	clear(drw);
	drw_text_clip(drw, 0, 0, WIDTH, HEIGHT, 0, "x\xf0\x9f\x98\x80", 0);
	prefix_actual = image(drw, argv[1], "actual-prefix-glyph");
	clear(drw);
	drw_text(drw, 50, 0, 30, HEIGHT, 0, "next", 0);
	sentinel = image(drw, argv[1], "ordinary-after-clear");
	clear(drw);
	drw_text_clip(drw, 0, 0, WIDTH, HEIGHT, 0, glyph, 0);
	drw_text(drw, 50, 0, 30, HEIGHT, 0, "next", 0);
	after_clip = image(drw, argv[1], "ordinary-after-clip");
	for (i = 0; i < HEIGHT; i++)
		for (int x = 50; x < 80; x++)
			need(XGetPixel(sentinel, x, i) == XGetPixel(after_clip, x, i),
			     "clip state leaked into subsequent ordinary drawing");

	same = mismatch(actual, expected, WIDTH, HEIGHT);
	wrong = mismatch(actual, ellipsis, WIDTH, HEIGHT);
	empty = mismatch(actual, blank, WIDTH, HEIGHT);
	ordinary_diff = mismatch(ordinary, ellipsis, WIDTH, HEIGHT);
	prefix_diff = mismatch(prefix_actual, prefix, WIDTH, HEIGHT);
	leak = 0;
	for (i = 0; i < 32; i++)
		for (int x = 0; x < 80; x++)
			if (x >= WIDTH || i >= HEIGHT)
				leak += XGetPixel(actual, x, i) != XGetPixel(blank, x, i);
	printf("{\"advance\":%u,\"glyph_mismatch\":%u,\"ellipsis_difference\":%u,"
	       "\"blank_difference\":%u,\"ordinary_ellipsis_mismatch\":%u,"
	       "\"prefix_mismatch\":%u,\"outside_pixels\":%u}\n",
	       advance, same, wrong, empty, ordinary_diff, prefix_diff, leak);
	need(!same && wrong && empty && !ordinary_diff && !prefix_diff && !leak,
	     "glyph content, ordinary ellipsis or confinement differs from independent Xft oracle");
	XDestroyImage(expected); XDestroyImage(actual); XDestroyImage(ellipsis);
	XDestroyImage(ordinary); XDestroyImage(blank); XDestroyImage(prefix);
	XDestroyImage(prefix_actual); XDestroyImage(sentinel); XDestroyImage(after_clip);
	drw_scm_free(drw, drw->scheme, 2);
	drw_free(drw);
	XCloseDisplay(display);
	return 0;
}
