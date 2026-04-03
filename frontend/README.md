This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## GTM and AdSense configuration

Set the following public environment variables for the Next.js frontend:

```bash
NEXT_PUBLIC_GTM_ID=GTM-XXXXXXX
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-XXXXXXXXXXXXXXXX
NEXT_PUBLIC_POSTHOG_KEY=phc_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
NEXT_PUBLIC_POSTHOG_HOST=https://eu.i.posthog.com
NEXT_PUBLIC_UMAMI_WEBSITE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
NEXT_PUBLIC_UMAMI_SCRIPT_URL=https://cloud.umami.is/script.js
NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION=google-site-verification-token
```

Where to add them:

- Local development: create `frontend/.env.local`
- GitHub Actions deployments: store them as repository secrets (`NEXT_PUBLIC_GTM_ID`, `NEXT_PUBLIC_ADSENSE_CLIENT_ID`, `NEXT_PUBLIC_POSTHOG_KEY`, `NEXT_PUBLIC_POSTHOG_HOST`, `NEXT_PUBLIC_UMAMI_WEBSITE_ID`, `NEXT_PUBLIC_UMAMI_SCRIPT_URL`); deploy workflows pass them as frontend Docker build args
- Platform hosting (e.g. Vercel): add them in project environment settings

Notes:

- GTM uses the "Analyse" consent toggle.
- AdSense loads from the root layout when the publisher ID is configured.
- The frontend serves `/ads.txt` automatically from `NEXT_PUBLIC_ADSENSE_CLIENT_ID`.
- If Google Search Console verification is needed, set `NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION` and the root layout will emit the verification meta tag.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
