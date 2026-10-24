# Native image to local project

Use the current image-generation and Drive tools when the task requires a local original and the user has selected Drive as the transfer route.

1. Generate the image in the owning Worker, inspect it, and retain its returned file identity.
2. Upload that real file URI to the user-selected Drive destination through the available upload tool.
3. Use the returned Drive file identity and an authorized download route in the current Managed Runner request.
4. Save the original bytes in the current task's output directory. Check the download status, content type, file size, decoding and hash when an expected hash is available.
5. Return the actual local file and retained transfer identity. Apply the current user's cleanup preference to temporary relay files.

Use task-local script inputs and the generic managed-host-script workflow. Keep tokens, private file identities and original image contents in the current installation.
