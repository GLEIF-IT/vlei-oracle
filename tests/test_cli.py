"""The oracle exposes only the trusted-status service model."""

import pytest

from vlei_oracle.cli import main


def test_combined_service_command_is_rejected(capsys):
    with pytest.raises(SystemExit) as error:
        main(["server", "legacy-start"])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["challenge", "client"])
def test_issuer_commands_are_absent(command, capsys):
    with pytest.raises(SystemExit) as error:
        main([command])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
