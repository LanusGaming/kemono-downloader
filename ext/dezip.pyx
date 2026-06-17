# cython: language_level=3
# distutils: language = c
# cython: cdivision = True
# cython: boundscheck = False
# cython: wraparound = False
# cython: nonecheck = False
# cython: profile = False

# Author: Zylo117
"""
cython implementation of zip decryption
"""

from cython.view cimport array as cvarray


def _gen_crc(long crc):
    cdef int j
    cdef long a = 0xEDB88320
    for j in range(8):
        if crc & 1:
            crc = (crc >> 1) ^ a
        else:
            crc >>= 1
    return crc

def _ZipDecrypter_C(bytes pwd):
    cdef long key0 = 305419896
    cdef long key1 = 591751049
    cdef long key2 = 878082192

    cdef int p, i

    # cdef list crctable = list(map(_gen_crc, range(256)))
    cdef long[:] crctable = cvarray(shape=(256,), itemsize=sizeof(long), format="l")
    for i in range(256):
        crctable[i] = _gen_crc(i)

    def crc32(long ch, long crc):
        cdef int a = 8
        cdef int b = 0xFF
        """Compute the CRC32 primitive on one byte."""
        return (crc >> a) ^ crctable[(crc ^ ch) & b]

    def update_keys(long c):
        nonlocal key0, key1, key2
        cdef int a = 0xFF
        cdef long b = 0xFFFFFFFF
        cdef int d = 134775813
        key0 = crc32(c, key0)
        key1 = (key1 + (key0 & a)) & b
        key1 = (key1 * d + 1) & b
        key2 = crc32(key1 >> 24, key2)

    for p in pwd:
        update_keys(p)

    def decrypter(bytes data):
        """Decrypt a bytes object."""
        cdef long k
        cdef unsigned int c
        cdef unsigned char cb

        cdef bytearray result = bytearray()
        for c in data:
            k = key2 | 2
            c ^= ((k * (k ^ 1)) >> 8) & 0xFF
            update_keys(c)
            cb = c
            result.append(cb)
        return bytes(result)

    return decrypter