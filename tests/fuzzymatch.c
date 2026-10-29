/* Exercise the real matching code without an X server. See README. */
#include <assert.h>
#define main dmenu_main
#include "../dmenu.c"
#undef main

int
main(int argc, char **argv)
{
	char *defaults[] = { "fuzzymatch-check", "ab", "axb", "zzab", "ab" };
	struct item *it, *last = NULL;
	size_t count = 0, nitems;
	int i = 1, selftest = argc == 1;

	if (selftest) {
		argc = LENGTH(defaults);
		argv = defaults;
	}
	for (; i < argc && argv[i][0] == '-'; ++i) {
		if (!strcmp(argv[i], "-i")) {
			fstrncmp = strncasecmp;
			fstrstr = cistrstr;
		} else if (!strcmp(argv[i], "-F")) {
			fuzzy = 0;
		} else if (!strcmp(argv[i], "--")) {
			++i;
			break;
		} else {
			return 2;
		}
	}
	assert(i < argc && strlen(argv[i]) < sizeof text);
	strcpy(text, argv[i++]);
	nitems = argc - i;
	items = ecalloc(nitems + 1, sizeof *items);
	for (int j = 0; i < argc; ++i, ++j)
		items[j].text = argv[i];
	/* Vertical geometry keeps calcoffsets independent of X font metrics. */
	lines = 10;
	bh = 1;
	match();
	assert(curr == matches && sel == matches);
	if (selftest) {
		assert(matches == &items[2]);
		assert(items[2].right == &items[1]);
		assert(items[1].right == &items[0]);
		assert(matchend == &items[0] && !matchend->right);
		assert(fabs(items[2].distance - (log(2) - 1)) < 1e-12);
		assert(fabs(items[1].distance - (log(4) - 1)) < 1e-12);
		assert(fabs(items[0].distance - log(2)) < 1e-12);
	}
	for (it = matches; it; it = it->right) {
		assert(++count <= nitems);
		assert(it->left == last);
		printf("%td\t%.17g\t%s\n", it - items, it->distance, it->text);
		last = it;
	}
	assert(last == matchend);
	for (it = matchend; it; it = it->left) {
		assert(count > 0);
		--count;
	}
	assert(count == 0);
	free(items);
	return 0;
}
