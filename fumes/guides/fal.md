# fal.ai

Fumes reads your **fal balance** from the billing API and your **spend per endpoint** from the usage
API.

## Get a key

1. Sign in at [fal.ai](https://fal.ai/dashboard/keys) and open **Keys**.
2. Create a key named `Fumes` with the **Admin** scope: the billing and usage APIs refuse a key that
   is only allowed to call models.
3. Copy it (it is shown only once), paste it into the key field here and choose **Test**.

Fumes keeps the key in Windows Credential Manager and sends it only to `api.fal.ai`.

## What you will see

- **Balance**: fal's own credit balance.
- **Spend**: what each endpoint cost per day, for the month so far and the last 30 days.
