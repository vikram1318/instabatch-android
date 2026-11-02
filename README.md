# InstaBatch

Android client and Flask backend source are in [`InstaBatch_Android/`](InstaBatch_Android/).

## Deploy the backend to Render

1. Push this repository to GitHub.
2. In Render, choose **New > Blueprint** and select the repository. Render reads this root `render.yaml`, which deploys `InstaBatch_Android/backend`.
3. Set `APP_PASSWORD` in the Render service environment. The Blueprint generates `SECRET_KEY` automatically; keep both values private.
4. After the service is live, enter its HTTPS URL and the app password in the Android app.

This Blueprint uses Render's free plan and temporary storage. It is suitable only for a small beta: restarts can delete files and job state, and the shared password is not a public user-account system. See [the project README](InstaBatch_Android/README.md) for backend limits and local development.