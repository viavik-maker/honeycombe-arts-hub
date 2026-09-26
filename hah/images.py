"""Checks for images uploaded through the admin (stdlib only).

sniff() works out the real type from the file's first bytes, so a renamed
file can't pass as an image. strip_jpeg_metadata() removes EXIF, XMP, IPTC
and comments — phone photos carry GPS location, device and time details — but
keeps the rotation flag so portrait photos still display the right way up."""
import struct

TYPES = {  # sniffed type -> file extension we store it under
    "jpeg": ".jpg",
    "png": ".png",
    "gif": ".gif",
    "webp": ".webp",
}


def sniff(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


# markers without a length field
_STANDALONE = {0x01, 0xD8, 0xD9} | set(range(0xD0, 0xD8))
# APP1 (EXIF, XMP), APP13 (Photoshop / IPTC), COM (comments)
_DROP = {0xE1, 0xED, 0xFE}


def _orientation(exif):
    """Orientation (1-8) from an APP1 EXIF payload, or None."""
    if not exif.startswith(b"Exif\x00\x00") or len(exif) < 14:
        return None
    tiff = exif[6:]
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if not order:
        return None
    try:
        (ifd,) = struct.unpack(order + "I", tiff[4:8])
        (count,) = struct.unpack(order + "H", tiff[ifd:ifd + 2])
        for i in range(count):
            at = ifd + 2 + 12 * i
            tag, typ = struct.unpack(order + "HH", tiff[at:at + 4])
            if tag == 0x0112 and typ == 3:
                (value,) = struct.unpack(order + "H", tiff[at + 8:at + 10])
                return value if 1 <= value <= 8 else None
    except struct.error:
        return None
    return None


def _orientation_segment(value):
    """A minimal APP1 EXIF segment holding only the orientation tag."""
    payload = (b"Exif\x00\x00" + b"MM\x00\x2a\x00\x00\x00\x08"   # big-endian TIFF, IFD0 at 8
               + b"\x00\x01"                                        # one entry
               + struct.pack(">HHIHH", 0x0112, 3, 1, value, 0)      # Orientation, SHORT, 1
               + b"\x00\x00\x00\x00")                               # no next IFD
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def strip_jpeg_metadata(data):
    """The same JPEG without metadata segments. Raises ValueError if unreadable."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    out, i, orientation, insert_at = [b"\xff\xd8"], 2, None, 1
    while True:
        if i + 2 > len(data) or data[i] != 0xFF:
            raise ValueError("corrupt JPEG")
        while data[i + 1] == 0xFF:  # fill bytes
            i += 1
        marker = data[i + 1]
        if marker in _STANDALONE:
            out.append(data[i:i + 2])
            i += 2
            continue
        if i + 4 > len(data):
            raise ValueError("corrupt JPEG")
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        end = i + 2 + length
        if length < 2 or end > len(data):
            raise ValueError("corrupt JPEG")
        if marker == 0xDA:  # start of scan: the image data runs to the end
            out.append(data[i:])
            break
        if marker in _DROP:
            if marker == 0xE1 and orientation is None:
                orientation = _orientation(data[i + 4:end])
        else:
            out.append(data[i:end])
            if marker == 0xE0 and insert_at == len(out) - 1:  # keep JFIF header first
                insert_at = len(out)
        i = end
    if orientation and orientation != 1:
        out.insert(insert_at, _orientation_segment(orientation))
    return b"".join(out)
