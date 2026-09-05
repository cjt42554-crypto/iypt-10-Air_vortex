"""Small synthetic observation checks, without experimental eligibility."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np

from airvortex.analysis._common import DataValidationError,RoleViolationError
from airvortex.io.measurements import load_measurement_contract,ensure_measurement_use
from airvortex.analysis.observation import (
    PlanarCoordinateCalibration,map_pixel_footprints_to_container,
    GlobalShutterSchedule,apply_global_shutter,
    solver_to_container_xy,container_to_solver_xy,
    CPInitialStateQualification,measured_profile_to_cp_initial_state,
)
from airvortex.core import SimulationConfig
from airvortex.backends.forward_topology import _build_geometry

FIXTURE=Path(__file__).resolve().parents[2]/'04_实验与数据/实验输入合同_SYNTHETIC.json'


class ObservationPreparationTests(unittest.TestCase):
    def test_synthetic_fixture_loads_but_cannot_fit_or_score(self):
        contract=load_measurement_contract(FIXTURE)
        self.assertEqual(contract.evidence_kind,'SYNTHETIC')
        ensure_measurement_use(contract,'software_test')
        for use in ['calibration_fit','sealed_validation_score']:
            with self.assertRaises(RoleViolationError):
                ensure_measurement_use(contract,use)
        raw=json.loads(FIXTURE.read_text())
        raw['split']['sealed_validation_run_ids']=[raw['run_id']]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.json';p.write_text(json.dumps(raw))
            with self.assertRaises(RoleViolationError):
                load_measurement_contract(p)

    def test_affine_handedness_area_rotation_and_scope(self):
        cal=PlanarCoordinateCalibration('synthetic',np.array([[.001,0,-.002],[0,-.001,.002],[0,0,1.]]),(0.,4.,0.,4.),-1,'SYNTHETIC_TEST_ONLY','SYNTHETIC')
        uv=[[2,2],[3,2],[2,1]]
        with self.assertRaises(DataValidationError):
            map_pixel_footprints_to_container(uv,[1,1,1],cal)
        mapped=map_pixel_footprints_to_container(uv,[1,1,1],cal,allow_synthetic_test=True)
        np.testing.assert_allclose(mapped.container_xy_m,[[0,0],[.001,0],[0,.001]],atol=1e-16)
        np.testing.assert_allclose(mapped.projected_area_m2,1e-6,rtol=1e-12)
        point=np.array([[.001,0.]])
        rotated=solver_to_container_xy(point,np.pi/2)
        np.testing.assert_allclose(rotated,[[0,.001]],atol=1e-16)
        np.testing.assert_allclose(container_to_solver_xy(rotated,np.pi/2),point,atol=1e-16)
        with self.assertRaisesRegex(DataValidationError,'HANDEDNESS'):
            map_pixel_footprints_to_container(uv,[1,1,1],replace(cal,expected_jacobian_sign=1),allow_synthetic_test=True)

    def test_exposure_preserves_source_knots_and_weighted_integral(self):
        shutter=GlobalShutterSchedule(np.array([0.]),np.array([1.]),np.array([0.,1.]),np.array([1.,1.]))
        # A triangular signal vanishes at both shutter endpoints but averages .5.
        r=apply_global_shutter([0,.5,1],[0,1,0],shutter)
        np.testing.assert_allclose(r.values,[.5],atol=1e-14)
        # Integral(t*t)/integral(t)=2/3; product of linear signal and response.
        weighted=replace(shutter,response_weight=np.array([0.,1.]))
        r=apply_global_shutter([0,1],[0,1],weighted)
        np.testing.assert_allclose(r.values,[2/3],atol=1e-14)
        np.testing.assert_allclose(r.effective_time_s,[2/3],atol=1e-14)
        with self.assertRaisesRegex(DataValidationError,'OUTSIDE_SOURCE_TIME'):
            apply_global_shutter([.1,1],[0,1],shutter)

    def test_exposure_sine_amplitude_matches_sinc(self):
        t=np.linspace(0,1,10001)
        shutter=GlobalShutterSchedule(np.array([.2]),np.array([.3]),np.array([0.,1.]),np.array([1.,1.]))
        measured=apply_global_shutter(t,np.sin(2*np.pi*t),shutter).values[0]
        self.assertAlmostEqual(measured,np.sinc(.1),delta=1e-7)

    def test_measured_initial_state_refuses_unqualified_mapping(self):
        c=SimulationConfig(); geometry,_=_build_geometry(c)
        q=CPInitialStateQualification('SYNTHETIC_TEST_ONLY','SYNTHETIC',True,True,True,False,True)
        with self.assertRaisesRegex(DataValidationError,'AXISYMMETRY'):
            measured_profile_to_cp_initial_state([0.,c.container.radius_m],
                [c.container.liquid_depth_m]*2,geometry,
                interface_thickness_m=.001,measured_liquid_volume_m3=geometry.target_liquid_volume_m3,
                mapped_volume_relative_tolerance=.001,configured_volume_relative_tolerance=.001,
                profile_mapping_tolerance_m=.001,qualification=q,allow_synthetic_test=True)

if __name__=='__main__':
    unittest.main()
