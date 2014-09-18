/* Derived from CPython 3.3.5's operator.c::_tscmp
 */

#include <stdlib.h>
#include "tscmp.h"

int
pypy_tscmp(const char *a, const char *b, long len_a, long len_b)
{
    /* The volatile type declarations make sure that the compiler has no
     * chance to optimize and fold the code in any way that may change
     * the timing.
     */
    volatile long length;
    volatile const char *left;
    volatile const char *right;
    long i;
    char result;

    /* loop count depends on length of b */
    length = len_b;
    left = NULL;
    right = b;

    /* don't use else here to keep the amount of CPU instructions constant,
     * volatile forces re-evaluation
     *  */
    if (len_a == length) {
        left = *((volatile const char**)&a);
        result = 0;
    }
    if (len_a != length) {
        left = b;
        result = 1;
    }

    for (i=0; i < length; i++) {
        result |= *left++ ^ *right++;
    }

    return (result == 0);
}