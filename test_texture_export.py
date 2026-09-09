import base64
import os
import tempfile
import unittest
from io import BytesIO

from PIL import Image

from pz_core.pz_export import export_textures_png


class Model:
    name = "synthetic"
    materials = []


def data_uri(image):
    buf = BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class TextureExportTest(unittest.TestCase):
    def test_exports_every_serialized_texture_and_preserves_alpha(self):
        fully_transparent = Image.new("RGBA", (1, 1), (255, 0, 0, 0))
        partially_transparent = Image.new("RGBA", (1, 1), (0, 255, 0, 96))

        with tempfile.TemporaryDirectory() as output_dir:
            saved = export_textures_png(
                Model(),
                [
                    {"data_uri": data_uri(fully_transparent)},
                    {"data_uri": data_uri(partially_transparent)},
                ],
                output_dir,
            )

            self.assertEqual(len(saved), 2)
            self.assertEqual(len(os.listdir(output_dir)), 2)
            with Image.open(os.path.join(output_dir, saved[1][0])) as exported:
                self.assertEqual(exported.getpixel((0, 0))[3], 96)

    def test_rejects_missing_serialized_data(self):
        with tempfile.TemporaryDirectory() as output_dir:
            with self.assertRaisesRegex(ValueError, "missing or empty data_uri"):
                export_textures_png(Model(), [{"data_uri": ""}], output_dir)


if __name__ == "__main__":
    unittest.main()
