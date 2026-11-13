# Manual provider

For an account Fumes cannot read: a prepaid card, a reseller, a service with no billing API. You
enter the balance yourself and Fumes shows it on the strip like any other.

## Add one

1. In Settings, choose **Add provider**, then **Manual**.
2. Give it a short name (it is shown on the strip) and an id (lowercase, used for its logo file).
3. Optionally, set a **daily burn**: what it costs you on a typical day. Fumes then counts the
   balance down by that much each day and draws it on the chart as an estimate.
4. Open its page and enter the **credit balance** the service shows now.

Enter the balance again after each top-up, or whenever the real figure has drifted from the
estimate. Without a daily burn, the balance simply stays as you entered it.

To give it a logo, save an SVG or PNG as `%APPDATA%\fumes\logos\<id>.svg` (or `.png`).
