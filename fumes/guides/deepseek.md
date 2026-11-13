# DeepSeek

Fumes reads your **DeepSeek balance** from DeepSeek's balance API.

## Get a key

1. Sign in at [platform.deepseek.com](https://platform.deepseek.com/api_keys) and open **API keys**.
2. Choose **Create new API key**, name it `Fumes`, and copy it (it is shown only once).
3. Paste it into the key field here and choose **Test**.

Any DeepSeek API key works; the balance call is free.

## What you will see

- **Balance**: DeepSeek's own figure, in US dollars.
- **Spend**: DeepSeek has no usage-history API, so Fumes estimates spend from the falls in your
  balance between its checks. It starts counting when you add the key, so the chart fills in over
  the following days. A top-up raises the balance and is not counted as spend.
