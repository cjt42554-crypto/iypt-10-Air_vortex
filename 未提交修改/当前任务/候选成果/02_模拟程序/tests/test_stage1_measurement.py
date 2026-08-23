import unittest, numpy as np
from airvortex.stage1.measurement import CameraModel, project_phase_to_camera
class TestMeasurement(unittest.TestCase):
    def test_operator_identity_and_determinism(self):
        q=np.ones((8,6,10)); q[:,:,7:]=0; q[3:5,:,4:8]=0; camera=CameraModel(pixel_size_m=1e-3,psf_sigma_pixels=0.5); a=project_phase_to_camera(q,spacing_x_m=1e-3,spacing_y_m=1e-3,spacing_z_m=1e-3,camera=camera); b=project_phase_to_camera(q,spacing_x_m=1e-3,spacing_y_m=1e-3,spacing_z_m=1e-3,camera=camera); np.testing.assert_array_equal(a["segmented_gas"],b["segmented_gas"]); self.assertEqual(a["metadata"]["measurement_operator_id"],camera.operator_id)
if __name__=="__main__": unittest.main()
