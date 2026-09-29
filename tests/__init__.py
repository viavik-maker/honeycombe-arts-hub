# Import the harness first: it points the app at a throwaway data folder
# (HAH_DATA_DIR) before any test module imports the app, so running a single
# test file can never touch the real data/ folder.
from tests import support  # noqa: F401
