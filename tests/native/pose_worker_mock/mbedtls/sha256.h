#include <stddef.h>
int mbedtls_sha256_ret(const unsigned char *, size_t, unsigned char out[32], int);
int mbedtls_sha256(const unsigned char *, size_t, unsigned char out[32], int);
