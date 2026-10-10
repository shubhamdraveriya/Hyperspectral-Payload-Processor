"""MSB-first bit packing and unpacking."""


class BitWriter:
    def __init__(self):
        self._out = bytearray()
        self._acc = 0
        self._nbits = 0

    def write(self, value, nbits):
        """Append the nbits least significant bits of value, MSB first."""
        if nbits == 0:
            return
        if value < 0 or value >> nbits:
            raise ValueError(f"{value} does not fit in {nbits} bits")
        self._acc = (self._acc << nbits) | value
        self._nbits += nbits
        while self._nbits >= 8:
            self._nbits -= 8
            self._out.append((self._acc >> self._nbits) & 0xFF)
        self._acc &= (1 << self._nbits) - 1

    def write_unary_zeros(self, count):
        """count zero bits."""
        while count > 32:
            self.write(0, 32)
            count -= 32
        self.write(0, count)

    @property
    def bit_length(self):
        return 8 * len(self._out) + self._nbits

    def pad_to_bytes(self, multiple=1):
        """Zero-fill to a byte boundary, then to a multiple of `multiple` bytes."""
        if self._nbits:
            self.write(0, 8 - self._nbits)
        while len(self._out) % multiple:
            self._out.append(0)

    def getvalue(self):
        if self._nbits:
            raise ValueError("bitstream not byte aligned; call pad_to_bytes() first")
        return bytes(self._out)


class BitReader:
    def __init__(self, data, start_byte=0):
        self._data = data
        self._pos = 8 * start_byte

    @property
    def bit_position(self):
        return self._pos

    def read(self, nbits):
        value = 0
        for _ in range(nbits):
            byte = self._data[self._pos >> 3]
            value = (value << 1) | ((byte >> (7 - (self._pos & 7))) & 1)
            self._pos += 1
        return value

    def count_zeros(self, limit):
        """Read zero bits up to `limit`; consume the terminating one if it comes first.

        Returns the number of zeros read (== limit means no terminating one was read).
        """
        n = 0
        while n < limit:
            byte = self._data[self._pos >> 3]
            bit = (byte >> (7 - (self._pos & 7))) & 1
            self._pos += 1
            if bit:
                return n
            n += 1
        return n
