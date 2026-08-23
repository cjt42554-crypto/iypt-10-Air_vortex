import unittest
from airvortex.stage1.contracts import ContractError, assert_observables_compatible, contract_document
class TestContracts(unittest.TestCase):
    def test_same_identity_allowed(self):
        a={"backend_id":"forward","observable_contract":contract_document(["surface_depth_m"])}; b={"backend_id":"hf","observable_contract":contract_document(["surface_depth_m"])}; assert_observables_compatible(a,b,"surface_depth_m")
    def test_changed_reference_rejected(self):
        a={"observable_contract":contract_document(["surface_depth_m"])}; b={"observable_contract":contract_document(["surface_depth_m"])}; b["observable_contract"]["definitions"]["surface_depth_m"]["identity"][3]="wrong_reference"
        with self.assertRaises(ContractError): assert_observables_compatible(a,b,"surface_depth_m")
if __name__=="__main__": unittest.main()
