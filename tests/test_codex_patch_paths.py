"""A patch's whole mutation set is the scope guard's input."""
import unittest
from kernel.analysis.patch_paths import InvalidPatch, paths


class PatchPaths(unittest.TestCase):
    def test_add_update_delete_and_rename_are_all_present(self):
        text = ("*** Begin Patch\n*** Add File: a.py\n+new\n"
                "*** Update File: b.py\n*** Move to: c.py\n@@\n-old\n+new\n"
                "*** Delete File: d.py\n*** End Patch")
        self.assertEqual(paths(text), ["a.py", "b.py", "c.py", "d.py"])

    def test_header_looking_added_content_is_not_a_file_operation(self):
        self.assertEqual(paths("*** Begin Patch\n*** Add File: a.py\n"
                               "+*** Delete File: secret.py\n*** End Patch"), ["a.py"])

    def test_malformed_payloads_cannot_return_an_empty_allowed_set(self):
        for value in (None, "", "*** Begin Patch\n*** End Patch",
                      "*** Begin Patch\n*** Add File: a.py\nbad body\n*** End Patch",
                      "*** Begin Patch\n*** Move to: a.py\n*** End Patch",
                      "*** Begin Patch\n*** Delete File: ../x\n*** End Patch"):
            with self.subTest(value=value), self.assertRaises(InvalidPatch):
                paths(value)

    def test_empty_files_and_rename_only_still_have_guarded_paths(self):
        self.assertEqual(paths("*** Begin Patch\n*** Add File: empty.py\n*** End Patch"), ["empty.py"])
        self.assertEqual(paths("*** Begin Patch\n*** Update File: old.py\n*** Move to: new.py\n*** End Patch"), ["old.py", "new.py"])

    def test_spaces_and_unicode_are_paths_not_shell_words(self):
        self.assertEqual(paths("*** Begin Patch\n*** Delete File: my 檔案.py\n*** End Patch"),
                         ["my 檔案.py"])
