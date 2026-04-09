"""HF Hub communication layer for the training pipeline.

Manages all interactions with HuggingFace Hub: checkpoint relay,
dataset sync, training state, and pipeline config.
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class HubRelay:
    """Manages HF Hub interactions for cross-platform training."""

    def __init__(self, model_repo: str, dataset_repo: str, token: Optional[str] = None):
        self.model_repo = model_repo
        self.dataset_repo = dataset_repo
        self._api = None
        self._token = token

    @property
    def api(self):
        if self._api is None:
            from huggingface_hub import HfApi
            self._api = HfApi(token=self._token)
        return self._api

    def ensure_repos(self):
        """Create Hub repos if they don't exist."""
        for repo_id, repo_type in [
            (self.model_repo, "model"),
            (self.dataset_repo, "dataset"),
        ]:
            try:
                self.api.create_repo(
                    repo_id, private=True, exist_ok=True, repo_type=repo_type,
                )
                logger.info(f"Repo ready: {repo_id} ({repo_type})")
            except Exception as e:
                logger.error(f"Failed to create {repo_id}: {e}")
                raise

    # ------------------------------------------------------------------
    # Training state
    # ------------------------------------------------------------------

    def get_training_state(self) -> Optional[Dict]:
        """Download training_state.json from the model repo."""
        try:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(
                repo_id=self.model_repo,
                filename="training_state.json",
                token=self._token,
            )
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            logger.debug(f"Training state not available: {e}")
            return None

    def update_training_state(self, state: Dict):
        """Upload training state to Hub."""
        self.api.upload_file(
            path_or_fileobj=json.dumps(state, indent=2).encode(),
            path_in_repo="training_state.json",
            repo_id=self.model_repo,
            commit_message=f"Training state: step {state.get('step', '?')}, "
                           f"loss {state.get('final_loss', '?')}",
        )

    # ------------------------------------------------------------------
    # Pipeline config (shared across all platforms)
    # ------------------------------------------------------------------

    def get_pipeline_config(self) -> Optional[Dict]:
        """Download pipeline_config.json from Hub."""
        try:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(
                repo_id=self.model_repo,
                filename="pipeline_config.json",
                token=self._token,
            )
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            logger.debug(f"Pipeline config not available: {e}")
            return None

    def push_pipeline_config(self, config: Dict):
        """Push pipeline config so workers can read it."""
        self.api.upload_file(
            path_or_fileobj=json.dumps(config, indent=2).encode(),
            path_in_repo="pipeline_config.json",
            repo_id=self.model_repo,
            commit_message="Update pipeline config",
        )

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    def get_latest_checkpoint_id(self) -> Optional[str]:
        """Find the latest checkpoint directory in the model repo."""
        try:
            files = self.api.list_repo_files(self.model_repo)
            checkpoint_dirs = set()
            for f in files:
                if "checkpoint-" in f:
                    # Extract checkpoint dir name (e.g., "checkpoint-150")
                    parts = f.split("/")
                    for part in parts:
                        if part.startswith("checkpoint-"):
                            checkpoint_dirs.add(part)

            if not checkpoint_dirs:
                return None

            # Sort by step number
            sorted_ckpts = sorted(
                checkpoint_dirs,
                key=lambda x: int(x.split("-")[1]),
            )
            return sorted_ckpts[-1]
        except Exception as e:
            logger.debug(f"No checkpoints found: {e}")
            return None

    def get_loss_history(self) -> List[Dict]:
        """Extract loss history from training logs on Hub."""
        try:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(
                repo_id=self.model_repo,
                filename="loss_history.json",
                token=self._token,
            )
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            logger.debug(f"Loss history not available: {e}")
            return []

    def append_loss_history(self, entry: Dict):
        """Append a loss entry to the history on Hub."""
        history = self.get_loss_history()
        history.append(entry)
        self.api.upload_file(
            path_or_fileobj=json.dumps(history, indent=2).encode(),
            path_in_repo="loss_history.json",
            repo_id=self.model_repo,
            commit_message=f"Loss history: step {entry.get('step', '?')}",
        )

    def download_checkpoint(self, checkpoint_id: str, local_dir: Path) -> Path:
        """Download a specific checkpoint for GGUF export."""
        from huggingface_hub import snapshot_download

        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)

        path = snapshot_download(
            repo_id=self.model_repo,
            local_dir=str(local_dir),
            allow_patterns=[f"{checkpoint_id}/*", "*.json", "*.model"],
            token=self._token,
        )
        logger.info(f"Downloaded checkpoint {checkpoint_id} to {path}")
        return Path(path)

    # ------------------------------------------------------------------
    # Dataset sync
    # ------------------------------------------------------------------

    def sync_dataset(self, local_path: Path) -> bool:
        """Upload training dataset to HF Hub if changed.

        Uses file hash to detect changes and avoid redundant uploads.
        """
        local_path = Path(local_path)
        if not local_path.exists():
            logger.error(f"Dataset not found: {local_path}")
            return False

        # Compute local hash
        local_hash = self._file_hash(local_path)

        # Check remote hash
        remote_hash = None
        try:
            from huggingface_hub import hf_hub_download
            hash_path = hf_hub_download(
                repo_id=self.dataset_repo,
                filename="dataset_hash.txt",
                repo_type="dataset",
                token=self._token,
            )
            with open(hash_path) as f:
                remote_hash = f.read().strip()
        except (FileNotFoundError, OSError) as e:
            logger.debug(f"Remote hash not available: {e}")

        if local_hash == remote_hash:
            logger.info("Dataset unchanged, skipping upload")
            return True

        # Upload dataset
        logger.info(f"Uploading dataset ({local_path.stat().st_size / 1e6:.1f} MB)...")

        self.api.create_repo(
            self.dataset_repo, private=True, exist_ok=True, repo_type="dataset",
        )

        self.api.upload_file(
            path_or_fileobj=str(local_path),
            path_in_repo="train.jsonl",
            repo_id=self.dataset_repo,
            repo_type="dataset",
            commit_message=f"Dataset update: {local_hash[:8]}",
        )

        # Upload hash
        self.api.upload_file(
            path_or_fileobj=local_hash.encode(),
            path_in_repo="dataset_hash.txt",
            repo_id=self.dataset_repo,
            repo_type="dataset",
            commit_message="Update dataset hash",
        )

        logger.info("Dataset synced to Hub")
        return True

    def _file_hash(self, path: Path) -> str:
        """Compute SHA256 hash of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------------
    # GGUF export
    # ------------------------------------------------------------------

    def push_gguf(self, gguf_path: Path):
        """Push GGUF model to the GGUF repo."""
        gguf_repo = self.model_repo + "-gguf"
        self.api.create_repo(gguf_repo, private=True, exist_ok=True)

        self.api.upload_file(
            path_or_fileobj=str(gguf_path),
            path_in_repo=Path(gguf_path).name,
            repo_id=gguf_repo,
            commit_message=f"GGUF export: {Path(gguf_path).name}",
        )
        logger.info(f"Pushed GGUF to {gguf_repo}")
