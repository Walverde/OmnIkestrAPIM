import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from main import DebouncedProductNotifier, ProductFolderHandler


class DebouncedProductNotifierTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.watch_directory = Path(self.temp_dir.name)
        self.post = Mock()
        self.post.return_value.raise_for_status.return_value = None
        self.notifier = DebouncedProductNotifier(
            str(self.watch_directory),
            "http://backend",
            debounce_seconds=60,
            post=self.post,
        )

    def tearDown(self):
        self.notifier.flush_all()
        self.temp_dir.cleanup()

    def test_multiple_file_events_coalesce_by_product(self):
        for index in range(30):
            self.notifier.submit(
                "created",
                str(self.watch_directory / "Chair" / f"photo-{index}.jpg"),
                False,
            )

        self.assertEqual(len(self.notifier._pending), 1)
        self.notifier.flush_all()

        self.post.assert_called_once()
        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["path"], str(self.watch_directory / "Chair" / "photo-29.jpg"))

    def test_move_keeps_both_paths_after_later_modification(self):
        old_path = str(self.watch_directory / "Chair")
        new_path = str(self.watch_directory / "Chair renamed")
        self.notifier.submit("moved", old_path, True, new_path)
        self.notifier.submit("modified", f"{new_path}/product.yaml", False)
        self.notifier.flush_all()

        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["event"], "moved")
        self.assertEqual(payload["path"], old_path)
        self.assertEqual(payload["destination_path"], new_path)

    def test_modified_event_is_forwarded(self):
        handler = ProductFolderHandler(self.notifier)
        file_path = str(self.watch_directory / "Chair" / "product.yaml")
        handler.on_modified(SimpleNamespace(src_path=file_path, is_directory=False))
        self.notifier.flush_all()

        self.assertEqual(self.post.call_args.kwargs["json"]["event"], "modified")

    def test_reserved_archives_and_default_patterns_are_ignored(self):
        handler = ProductFolderHandler(self.notifier)
        ignored_paths = (
            self.watch_directory / "Chair" / "_arquivos" / "originais" / "source.psd",
            self.watch_directory / "Chair" / "~$draft.tmp",
            self.watch_directory / "Chair" / "Thumbs.db",
        )

        for path in ignored_paths:
            handler.on_created(SimpleNamespace(src_path=str(path), is_directory=False))

        self.assertEqual(self.notifier._pending, {})
        self.post.assert_not_called()

    def test_custom_ignore_patterns_are_applied(self):
        handler = ProductFolderHandler(self.notifier, ignore_patterns=("*.raw",))
        raw_file = self.watch_directory / "Chair" / "original.raw"

        handler.on_created(SimpleNamespace(src_path=str(raw_file), is_directory=False))

        self.assertEqual(self.notifier._pending, {})

    def test_underscore_prefixed_directories_are_ignored(self):
        handler = ProductFolderHandler(self.notifier)
        ignored_paths = (
            self.watch_directory / "Chair" / "_draft" / "cover.jpg",
            self.watch_directory / "Chair" / "_arquivos" / "originais" / "source.psd",
            self.watch_directory / "Chair" / "_cache",
        )

        for path in ignored_paths:
            handler.on_created(SimpleNamespace(src_path=str(path), is_directory=path.is_dir()))

        self.assertEqual(self.notifier._pending, {})
        self.post.assert_not_called()

    def test_move_from_indexed_path_into_ignored_path_becomes_delete(self):
        handler = ProductFolderHandler(self.notifier)
        source = self.watch_directory / "Chair" / "cover.jpg"
        destination = self.watch_directory / "Chair" / "_arquivos" / "cover.jpg"

        handler.on_moved(
            SimpleNamespace(
                src_path=str(source),
                dest_path=str(destination),
                is_directory=False,
            )
        )
        self.notifier.flush_all()

        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["event"], "deleted")
        self.assertEqual(payload["path"], str(source))
        self.assertIsNone(payload["destination_path"])

    def test_move_from_ignored_path_into_indexed_path_becomes_create(self):
        handler = ProductFolderHandler(self.notifier)
        source = self.watch_directory / "Chair" / "_arquivos" / "cover.jpg"
        destination = self.watch_directory / "Chair" / "cover.jpg"

        handler.on_moved(
            SimpleNamespace(
                src_path=str(source),
                dest_path=str(destination),
                is_directory=False,
            )
        )
        self.notifier.flush_all()

        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["event"], "created")
        self.assertEqual(payload["path"], str(destination))
        self.assertIsNone(payload["destination_path"])

    def test_nested_product_events_resolve_to_nearest_product(self):
        product_dir = self.watch_directory / "Eletrônicos" / "Notebooks" / "ThinkPad"
        reserved_images = product_dir / "images"
        reserved_images.mkdir(parents=True)
        (product_dir / "product.yaml").write_text("title: ThinkPad\n", encoding="utf-8")
        (product_dir / "front.jpg").write_bytes(b"front")
        reserved_image = reserved_images / "side.jpg"
        reserved_image.write_bytes(b"side")

        self.assertEqual(
            self.notifier._product_key(str(reserved_image)), str(product_dir)
        )

    def test_sibling_nested_products_have_distinct_debounce_keys(self):
        first = self.watch_directory / "Eletrônicos" / "Notebooks" / "ThinkPad"
        second = self.watch_directory / "Eletrônicos" / "Notebooks" / "Chromebook"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        (first / "cover.jpg").write_bytes(b"one")
        (second / "cover.jpg").write_bytes(b"two")

        self.notifier.submit("created", str(first / "cover.jpg"), False)
        self.notifier.submit("created", str(second / "cover.jpg"), False)

        self.assertEqual(len(self.notifier._pending), 2)

    def test_legacy_group_manifest_is_not_a_debounce_product(self):
        group = self.watch_directory / "Printers"
        nested_product = group / "Laser"
        nested_product.mkdir(parents=True)
        (group / "product.yaml").write_text(
            "schema_version: 1\nid: old-group-id\ntitle: Printers\n",
            encoding="utf-8",
        )
        (nested_product / "product.yaml").write_text(
            "schema_version: 1\nid: nested-product-id\ntitle: Laser\nsku: LASER\n",
            encoding="utf-8",
        )

        self.assertFalse(self.notifier._is_product_directory(group))
        self.assertTrue(self.notifier._is_product_directory(nested_product))


if __name__ == "__main__":
    unittest.main()