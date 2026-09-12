import unittest

from convert_epg import apply_playlist_aliases, convert_xml, format_sydney_timestamp


class TimestampConversionTests(unittest.TestCase):
    def test_aest_conversion(self):
        self.assertEqual(
            format_sydney_timestamp("20260913190000 +0800"),
            "20260913210000 +1000",
        )

    def test_aedt_conversion(self):
        self.assertEqual(
            format_sydney_timestamp("20261213190000 +0800"),
            "20261213220000 +1100",
        )

    def test_missing_offset_defaults_to_shanghai(self):
        self.assertEqual(
            format_sydney_timestamp("202609131900"),
            "202609132100 +1000",
        )

    def test_zulu_and_colon_offset_are_supported(self):
        self.assertEqual(
            format_sydney_timestamp("202609131900 +00:00"),
            "202609140500 +1000",
        )
        self.assertEqual(
            format_sydney_timestamp("202609131900Z"),
            "202609140500 +1000",
        )

    def test_programme_attributes_are_converted(self):
        source = (
            '<tv><programme channel="demo" start="202609131900 +0800" '
            'stop="202609131930 +0800"><title>Demo</title></programme></tv>'
        )
        converted, programmes, timestamps = convert_xml(source)
        self.assertEqual(programmes, 1)
        self.assertEqual(timestamps, 2)
        self.assertIn('start="202609132100 +1000"', converted)
        self.assertIn('stop="202609132130 +1000"', converted)

    def test_playlist_aliases_clone_channel_and_programmes(self):
        xml = (
            '<tv><channel id="CCTV1"><display-name>CCTV1</display-name></channel>'
            '<programme channel="CCTV1" start="202609132100 +1000" '
            'stop="202609132130 +1000"><title>Demo</title></programme></tv>'
        )
        playlist = '#EXTINF:-1 tvg-name="cctv1-AV3A",cctv1-AV3A\nhttps://example.invalid/stream\n'
        converted, aliases = apply_playlist_aliases(xml, playlist)
        self.assertEqual(aliases, {"cctv1-AV3A": "CCTV1"})
        self.assertIn('<channel id="cctv1-AV3A">', converted)
        self.assertIn('<programme channel="cctv1-AV3A"', converted)


if __name__ == "__main__":
    unittest.main()
