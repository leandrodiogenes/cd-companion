import unittest

from shared.map_layer import current_pywel_coordinates


class MapLayerTests(unittest.TestCase):
    def test_public_landmarks_on_current_layer(self):
        # Old CoderFX snapshot -> MapGenie public map 887 data, 2026-11-03.
        fixtures = [
            ('Hernand Castle', -.84049726123084, .62122330990415, -.6907587606, .8089055420),
            ('Bear', -.95082782809803, .57888780904149, -.7741546308, .7769065991),
            ('Rodbel Fishing Dock', -.82745391566520, .61445064075485, -.6808996509, .8037864752),
            ('Eastern Court', -.67951581504201, .65155708169852, -.5690772704, .8318330780),
        ]
        for name, old_lng, old_lat, expected_lng, expected_lat in fixtures:
            with self.subTest(landmark=name):
                lng, lat = current_pywel_coordinates(old_lng, old_lat)
                self.assertAlmostEqual(lng, expected_lng, delta=.000001)
                self.assertAlmostEqual(lat, expected_lat, delta=.000001)


if __name__ == '__main__':
    unittest.main()
