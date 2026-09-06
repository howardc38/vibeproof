import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from doc_impact import affected


class DocumentationImpact(unittest.TestCase):
    def test_a_new_kernel_module_does_not_disappear_from_review(self):
        result = affected(["kernel/new_transition.py"])
        self.assertEqual(result["docs/SPEC.md (new/unmapped source)"], ["kernel/new_transition.py"])

    def test_hook_changes_include_adoption_instructions(self):
        result = affected(["hooks/write_block.py", "README.md"])
        self.assertIn("docs/GETTING_STARTED*.md", result)
        self.assertEqual(result["docs/GETTING_STARTED*.md"], ["hooks/write_block.py"])

    def test_an_image_alone_does_not_claim_the_kernel_contract_changed(self):
        self.assertEqual(affected(["docs/launch/assets/hero.png"]), {})
