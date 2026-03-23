import struct


def create_wav_header(
    data_length: int,
    *,
    sample_rate: int = 16000,
    bits_per_sample: int = 16,
    channels: int = 1,
) -> bytes:
    """Create a PCM WAV header.

    The Node backend prepends a 44-byte RIFF/WAVE header to each emitted chunk.
    """

    byte_rate = int(sample_rate * channels * bits_per_sample / 8)
    block_align = int(channels * bits_per_sample / 8)
    file_size = data_length + 36

    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", file_size),
            b"WAVE",
            b"fmt ",
            struct.pack("<I", 16),
            struct.pack("<H", 1),
            struct.pack("<H", channels),
            struct.pack("<I", sample_rate),
            struct.pack("<I", byte_rate),
            struct.pack("<H", block_align),
            struct.pack("<H", bits_per_sample),
            b"data",
            struct.pack("<I", data_length),
        ]
    )
