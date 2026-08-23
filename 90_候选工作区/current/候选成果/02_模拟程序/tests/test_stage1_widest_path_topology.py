import unittest

import numpy as np

from airvortex.stage1.topology3d import _widest


class Grid:
    def __init__(self, shape=(11, 11, 12)) -> None:
        self.shape = shape
        self.nx, self.ny, self.nz = shape
        self.dx = self.dy = self.dz = 1.0


class Stage1WidestPathTopologyTests(unittest.TestCase):
    def test_widest_path_prefers_larger_bottleneck_and_detects_disconnect(self) -> None:
        grid = Grid()
        narrow = np.zeros(grid.shape, dtype=bool)
        narrow[5, 5, :] = True
        target = np.zeros(grid.shape, dtype=bool)
        target[5, 5, 0] = True
        narrow_capacity = _widest(narrow, target, grid)
        self.assertGreater(narrow_capacity, 0.0)

        wide = np.zeros(grid.shape, dtype=bool)
        wide[3:8, 3:8, :] = True
        wide_capacity = _widest(wide, target, grid)
        self.assertGreater(wide_capacity, narrow_capacity)

        disconnected = wide.copy()
        disconnected[:, :, 6] = False
        self.assertEqual(_widest(disconnected, target, grid), 0.0)


if __name__ == "__main__":
    unittest.main()
