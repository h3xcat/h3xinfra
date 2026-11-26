"""Static checks for the binding of the OTP reset-credentials flow.

reset_credentials.tftest.hcl checks the flow itself with a mocked provider.
terraform test cannot read resources inside a child module, so these plain
regex checks read the binding in mfa.tf and modules/mfa-flow/main.tf.
"""

from pathlib import Path
import re
import unittest


TERRAFORM = Path(__file__).resolve().parents[1] / "terraform"
MODULE = TERRAFORM / "modules" / "mfa-flow" / "main.tf"


def module_block(name):
    text = (TERRAFORM / "mfa.tf").read_text()
    match = re.search(r'^module "%s" \{\n(.*?)^\}$' % name, text, re.M | re.S)
    if match is None:
        raise AssertionError(f'module "{name}" not found in mfa.tf')
    return match.group(1)


def terraform_files():
    return [path for path in TERRAFORM.rglob("*.tf") if ".terraform" not in path.parts]


class ResetCredentialsBindingTests(unittest.TestCase):
    def test_application_realm_binds_the_otp_reset_flow(self):
        self.assertRegex(module_block("mfa_h3xinfra"),
                         r"(?m)^\s*reset_credentials_flow\s*=\s*keycloak_authentication_flow\.reset_otp\.alias$")
        self.assertRegex(MODULE.read_text(),
                         r"(?m)^\s*reset_credentials_flow\s*=\s*var\.reset_credentials_flow$")

    def test_master_realm_keeps_its_reset_flow(self):
        self.assertNotIn("reset_credentials_flow", module_block("mfa_master"))

    def test_binding_waits_for_every_step(self):
        # A partial flow (for example the deny subflow without its condition)
        # resets a password without the OTP check. The module must bind the
        # flow only after every execution and configuration exists.
        depends = re.search(r"depends_on\s*=\s*\[(.*?)\]", module_block("mfa_h3xinfra"), re.S)
        self.assertIsNotNone(depends, "mfa_h3xinfra must depend on the reset flow steps")
        listed = set(re.findall(r"keycloak_authentication_\w+\.\w+", depends.group(1)))
        steps = {f"{kind}.{name}" for kind, name in re.findall(
            r'^resource "(keycloak_authentication_execution(?:_config)?)" "(\w+)"',
            (TERRAFORM / "reset_credentials.tf").read_text(), re.M)}
        self.assertEqual(len(steps), 9)
        self.assertEqual(listed, steps)

    def test_one_bindings_resource_per_realm(self):
        # On create, a bindings resource sets each binding that it does not
        # give to the built-in flow. A second one resets the MFA browser flow.
        found = {path.relative_to(TERRAFORM).as_posix(): len(re.findall(
            r'^resource "keycloak_authentication_bindings"', path.read_text(), re.M))
            for path in terraform_files()}
        self.assertEqual({path: count for path, count in found.items() if count},
                         {"modules/mfa-flow/main.tf": 1})
        self.assertNotRegex((TERRAFORM / "realm.tf").read_text(), r"(?m)^\s*\w+_flow\s*=")

    def test_no_reset_otp_step(self):
        # The built-in "Reset OTP" step makes the user enroll a new OTP device.
        for path in terraform_files():
            self.assertNotIn('"reset-otp"', path.read_text(), path)


if __name__ == "__main__":
    unittest.main()
