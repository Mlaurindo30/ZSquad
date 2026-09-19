"""Script one-shot: aplica colunas SDLC nos boards de Features e Epics no Azure DevOps."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "integrations"))
sys.path.insert(0, str(ROOT / "scripts"))

from devops_platform_connector import DevOpsPlatformConnector  # noqa: E402
from azure_devops_project_setup import AzureDevOpsProjectSetup, load_setup_config  # noqa: E402


def main() -> None:
    connector = DevOpsPlatformConnector(root_path=ROOT)
    client = connector.client
    config = load_setup_config(ROOT)
    setup = AzureDevOpsProjectSetup(client, config)
    setup.discover()
    print(f"project_id: {setup.project_id}")
    print(f"team_id:    {setup.team_id}")
    print()
    print("=== Applying portfolio board columns (Features + Epics) ===")
    setup.apply_portfolio_board_columns()
    for r in setup.results:
        status = r["status"]
        step = r["step"]
        detail = r["detail"]
        print(f"[{status}] {step}: {detail}")


if __name__ == "__main__":
    main()
