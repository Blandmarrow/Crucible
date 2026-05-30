# Troubleshooting

## Changes not showing up / stale UI

If the app appears to be showing old data or not reflecting a recent change (wrong image counts, outdated captions, gallery not refreshing), the most likely cause is the browser serving a cached version of the frontend assets.

**Fix:** clear your browser cache for `localhost:8000`, or open the app in a private/incognito window. If the problem persists, do a hard refresh (`Ctrl+Shift+R` on Windows/Linux, `Cmd+Shift+R` on macOS).
