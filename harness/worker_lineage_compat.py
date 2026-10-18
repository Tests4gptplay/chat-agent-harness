"""Stable serialization names for Worker generation lineages.

Python APIs and documentation use ``lineage``; these historical wire values
remain unchanged so a naming-only refactor cannot orphan persisted state,
queued controls, or already-issued Worker/handoff identities. There is no
migration, secondary state, or alternate lifecycle behind this mapping.
"""

WORKER_LINEAGES_KEY = 'task_pools'
LAST_WORKER_REF_KEY = 'last_pool_takeover_id'
OUTGOING_WORKER_REF_KEY = 'outgoing_pool_id'
LANE_LINEAGE_RESET_KIND = 'lane_pool_reset'
LANE_LINEAGE_RESET_COMPLETE_OP = 'lane_pool_reset_complete'
LOCAL_LINEAGE_RESET_KEY = 'local_pool_reset'
TASK_LINEAGE_SCOPED_KEY = 'task_pool_scoped'
TASK_LINEAGE_OWNER = 'task_pool_owner'
TASK_LINEAGE_MISSING = 'task_pool_missing'
TASK_LINEAGE_IDENTITY_MISMATCH = 'task_pool_identity_mismatch'
HANDOFF_TASK_LINEAGE_MISSING = 'HANDOFF_TASK_POOL_MISSING'
NO_TASK_LINEAGE = 'no_task_pool'
WORKER_REF_PREFIX = 'pool-'
