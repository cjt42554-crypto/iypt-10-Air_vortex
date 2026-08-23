import unittest

from airvortex.stage1 import install_stage1_overlay
from airvortex.stage1.bootstrap import STAGE1_BACKEND_IMPORTS


class Stage1RegistryAndEntrypointsTests(unittest.TestCase):
    def test_registry_uses_valid_four_tuples_and_stage1_classes(self) -> None:
        install_stage1_overlay()
        install_stage1_overlay()
        self.assertTrue(all(len(item) == 4 for item in STAGE1_BACKEND_IMPORTS))

        # Importing the headless GUI model is part of this gate; no Tk display
        # or matplotlib backend is required.
        from airvortex.gui.model import GuiViewModel
        from airvortex.runtime import discover_backends

        GuiViewModel()
        registry = discover_backends(probe=True)
        self.assertEqual(registry.discovery_errors, {})
        self.assertEqual(registry.ids(), ("forward", "high_fidelity"))
        expected_modules = set()
        for key in (
            "forward",
            "f",
            "forward_theory",
            "stage1_forward",
            "high_fidelity",
            "hf",
            "high-fidelity",
            "stage1_hf3d",
        ):
            backend = registry.create(key)
            expected_modules.add(type(backend).__module__)
        self.assertEqual(expected_modules, {"airvortex.stage1.backends"})


if __name__ == "__main__":
    unittest.main()
