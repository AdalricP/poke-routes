"""The hidden-item glitter: a small four-point star that twinkles, drawn here pixel by
pixel in colours taken from the Arklarna outdoor palettes. Four 16x16 frames, side by
side in one strip; the Routes view plays them as frames 0 1 2 3 2 1."""
import io

from PIL import Image

FRAME_SIZE = 16
CENTRE = 7
TWINKLE_ORDER = [0, 1, 2, 3, 2, 1]

CORE_WHITE = (247, 247, 247, 255)
PALE_YELLOW = (255, 239, 165, 255)
SOFT_OUTLINE = (198, 156, 107, 255)
COOL_GLINT = (214, 239, 255, 255)

# arm length, whether the centre is a fat 3x3 diamond, how far out the diagonal glints sit
FRAME_SHAPES = [
    {"arm_length": 1, "fat_centre": False, "glint_distance": 0},
    {"arm_length": 2, "fat_centre": False, "glint_distance": 0},
    {"arm_length": 5, "fat_centre": True, "glint_distance": 3},
    {"arm_length": 3, "fat_centre": True, "glint_distance": 0},
]


def star_body(arm_length: int, fat_centre: bool) -> dict:
    body = {(0, 0): CORE_WHITE}
    for distance in range(1, arm_length + 1):
        colour = CORE_WHITE if distance <= arm_length // 2 else PALE_YELLOW
        for step_x, step_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            body[(step_x * distance, step_y * distance)] = colour
    if fat_centre:
        for corner in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            body[corner] = PALE_YELLOW
    return body


def soft_outline(body: dict) -> dict:
    outline = {}
    for offset_x, offset_y in body:
        for step_x, step_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbour = (offset_x + step_x, offset_y + step_y)
            if neighbour not in body:
                outline[neighbour] = SOFT_OUTLINE
    return outline


def diagonal_glints(glint_distance: int) -> dict:
    if not glint_distance:
        return {}
    return {(step_x * glint_distance, step_y * glint_distance): COOL_GLINT
            for step_x, step_y in ((1, 1), (1, -1), (-1, 1), (-1, -1))}


def glitter_frame(shape: dict) -> Image.Image:
    body = star_body(shape["arm_length"], shape["fat_centre"])
    pixels = {**soft_outline(body), **diagonal_glints(shape["glint_distance"]), **body}
    frame = Image.new("RGBA", (FRAME_SIZE, FRAME_SIZE), (0, 0, 0, 0))
    for (offset_x, offset_y), colour in pixels.items():
        frame.putpixel((CENTRE + offset_x, CENTRE + offset_y), colour)
    return frame


def glitter_strip() -> Image.Image:
    strip = Image.new("RGBA", (FRAME_SIZE * len(FRAME_SHAPES), FRAME_SIZE), (0, 0, 0, 0))
    for frame_index, shape in enumerate(FRAME_SHAPES):
        strip.paste(glitter_frame(shape), (frame_index * FRAME_SIZE, 0))
    return strip


def glitter_strip_png() -> bytes:
    buffer = io.BytesIO()
    glitter_strip().save(buffer, "PNG")
    return buffer.getvalue()


def glitter_preview(scale: int = 8, backdrop=(115, 173, 99, 255)) -> Image.Image:
    strip = glitter_strip()
    preview = Image.new("RGBA", strip.size, backdrop)
    preview.alpha_composite(strip)
    return preview.resize((strip.width * scale, strip.height * scale), Image.NEAREST)
