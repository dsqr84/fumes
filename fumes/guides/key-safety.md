# Keeping your keys safe

An API key is a password and a credit card in one: whoever has it can use the account it belongs to,
and spend your money doing it. Some of the keys Fumes asks for are **Admin keys**, which can do more
than spend: they can create and delete other keys, invite people to your organisation and change its
settings. Treat every key as something that must never be seen by anyone but you.

## Keep the damage small, before anything goes wrong

- **Make a key just for Fumes.** Name it `Fumes` when you create it, so you know what it is for and
  can revoke it without breaking anything else. Never reuse a key you use in your own code.
- **Use the narrowest key the provider offers.** If there is a read-only, usage-only or restricted
  option that still works with Fumes, choose it. Each provider page here says which kind Fumes needs.
- **Put a ceiling on your spending.** Most providers let you set a monthly budget, a spend limit or
  an email alert in their billing settings. Prepaid credit with automatic top-ups turned off is a hard
  cap: a leaked key cannot spend more than what is left.
- **Revoke keys you no longer use.** If you stop using Fumes, delete its keys on each provider's
  website, not only here.

## Where keys leak

Almost every leaked key is pasted or saved somewhere by its owner, not stolen. Never put a key:

- **into a chat**, including an AI assistant like Claude or ChatGPT, a support ticket, a forum post,
  Discord or Slack. A chat is stored, often for a long time, and can be read by more people than you
  think. To set Fumes up with an AI's help, let it run `fumes key set <provider>` and paste the key
  into that prompt yourself: the key goes straight into Windows Credential Manager and the AI never
  sees it.
- **into a file you share or commit.** A key committed to git stays in the history even after you
  delete the file, and private repositories become public, get forked or get shared. Bots scan public
  code for keys within minutes.
- **on a command line.** Commands are saved in your shell history. `fumes key set` asks for the key
  in a hidden prompt for this reason.
- **on screen.** Screenshots, screen recordings and screen sharing all show what is in a text box.
  Fumes never shows a saved key, but the provider's website does when you create one.

## What Fumes does with your keys

- They are stored in **Windows Credential Manager**, encrypted to your Windows account, as generic
  credentials named `Fumes/<provider>`. Fumes never writes a key to a file or a log, and never shows
  a saved one, not even in part.
- Each key is sent only to **its own provider's API**, over HTTPS, and only to read balances and
  spend. Fumes refuses to send a key that looks like it belongs to a different provider.
- **What this cannot protect against:** any program running as your Windows user can read your
  credentials, as it can read everything else you own. Malware on your computer can take your keys,
  whichever app stores them. Keep Windows updated and do not run programs you do not trust.

## If a key leaks, or you think it might have

1. **Revoke it on the provider's website now.** This is the only thing that stops it working.
   Deleting the file, the message or the commit does not.
2. **Create a new key** and save it in Fumes (Settings, the provider's page, Save).
3. **Check the provider's usage and billing pages** for spend or activity you do not recognise, and
   for keys or members you did not add (an Admin key can create them).
4. If you see spending that is not yours, **contact the provider's support**: many will help with
   charges from a leaked key reported quickly.
