# 📱 Getting Final Whistle onto your phone

There are two ways to test on a real Android phone. Both need the app **deployed
to a live HTTPS URL first** (the app talks to your Supabase backend at runtime —
it can't run from a file). Deploying to Vercel is free and takes ~5 minutes.

---

## Step 0 — Deploy (required for both paths)
1. Push `final-whistle/` to a GitHub repo and import it in [vercel.com](https://vercel.com)
   (set the **Root Directory** to `final-whistle`).
2. Add the 3 env vars from `.env.example` (Supabase URL, anon key, service-role key).
3. Add your Vercel domain to **Supabase → Auth → URL Configuration** redirect URLs,
   and to the Google OAuth client's authorized origins.
4. You now have e.g. `https://final-whistle-xyz.vercel.app`.

---

## Path A — Install as a PWA (fastest, no APK build) ✅ recommended for testing
On your Android phone:
1. Open the Vercel URL in **Chrome**.
2. Tap the **⋮ menu → "Install app"** (or "Add to Home Screen").
3. It installs a full-screen app icon. Google login works because it's real Chrome.

That's a genuine, installable, testable app on your phone in under a minute — no
signing, no Play Console, no toolchain.

---

## Path B — Build a real signed APK / AAB (TWA via Bubblewrap)
A **Trusted Web Activity** wraps your deployed PWA into a Play-Store-ready package.
> Do **not** use a Capacitor/Cordova WebView wrapper for this app — Google blocks
> OAuth in embedded WebViews (`disallowed_useragent`). TWA uses Chrome and works.

### Requirements (on your own machine, not this sandbox)
- Node 18+, JDK 17+, and **Android Studio** (provides the Android SDK).
- Bubblewrap auto-downloads the rest:
  ```bash
  npm install -g @bubblewrap/cli
  ```

### Build
```bash
# 1. Initialise from your deployed manifest (edit android/twa-manifest.template.json first):
bubblewrap init --manifest=https://YOUR-DOMAIN.vercel.app/manifest.webmanifest

#    …or use the provided template:
cp android/twa-manifest.template.json twa-manifest.json   # set host + URLs
bubblewrap init --manifest=./twa-manifest.json

# 2. Build (prompts to create a signing keystore the first time):
bubblewrap build
#    → produces app-release-signed.apk  and  app-release-bundle.aab
```

### Install the APK on a phone
```bash
adb install app-release-signed.apk
```
…or copy the `.apk` to the phone and tap it (enable "install unknown apps").

### Digital Asset Links (removes the browser URL bar)
Bubblewrap prints a SHA-256 fingerprint. Publish it so the TWA runs full-screen:
create `public/.well-known/assetlinks.json` on the deployed site with that
fingerprint, then redeploy. Bubblewrap shows the exact JSON to paste.

### Publish to Play Store
Upload the `.aab` to the [Play Console](https://play.google.com/console)
($25 one-time). Fill the store listing, content rating (it's a free game, no real
-money gambling — coins have no cash value), and roll out to internal testing.

---

## Why I can't hand you a prebuilt APK from here
An APK that "just plays" would need to embed **your** Supabase project, **your**
Google OAuth client, a **live deployed URL**, and an **app signing key** — all of
which are yours to own and keep secret. The TWA above produces exactly that APK in
two commands once the app is deployed.

**Want me to generate the TWA project for you?** Deploy (Step 0) and send me the
live URL — I'll fill in `twa-manifest.json` and the `assetlinks.json` for your
domain so all you run locally is `bubblewrap build`.
