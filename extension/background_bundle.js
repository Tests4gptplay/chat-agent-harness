'use strict';

// Chromium MV3 entrypoint for lane-oriented backend Worker routing.
// Foreground progress notifications are no longer part of the normal path.
importScripts(
  'control_transport.js',
  'lane_registry.js',
  'task_cell_registry.js',
  'ui_recovery_runtime.js',
  'background.js',
  'lane_worker_runtime.js',
  'lane_worker_retirement.js',
  'lane_clear_runtime.js'
);
