"""Unit tests for perceptual hashing used by the visual validator."""

from PIL import Image, ImageDraw

from webval.validators.visual import dhash, hamming


def _img(color: str, marks: int = 0) -> Image.Image:
    img = Image.new("RGB", (400, 300), color)
    draw = ImageDraw.Draw(img)
    for i in range(marks):
        draw.rectangle([20 * i, 30 * i, 20 * i + 60, 30 * i + 40], fill="black")
    return img


class TestDhash:
    def test_identical_images_distance_zero(self):
        assert hamming(dhash(_img("white", 3)), dhash(_img("white", 3))) == 0

    def test_scaling_invariance(self):
        original = _img("white", 3)
        scaled = original.resize((200, 150))
        assert hamming(dhash(original), dhash(scaled)) <= 8

    def test_different_layouts_large_distance(self):
        a = _img("white", 1)
        b = _img("white", 8)
        assert hamming(dhash(a), dhash(b)) > 24

    def test_hash_is_256_bits(self):
        assert dhash(_img("gray")).bit_length() <= 256
