# Getting your EcoFlow API keys

[Українська версія](getting-api-keys.uk.md)

The widget needs two keys from EcoFlow's developer platform: an **Access Key** and a **Secret Key**. They're free, but EcoFlow approves developer accounts manually, so it can take from a few hours to a few days.

## Before you start

Your station must be added in the **EcoFlow mobile app** and be online over Wi-Fi.

Note which account the app is logged into (profile icon in the app). **The developer account must be the same account.** The API only sees devices linked to that exact account. Registering the product on the ecoflow.com website is not the same as adding it in the app.

## Steps

1. Open the developer portal for your region:
   - Europe, including Ukraine: [developer-eu.ecoflow.com](https://developer-eu.ecoflow.com)
   - Other regions: [developer.ecoflow.com](https://developer.ecoflow.com)
2. Log in with the **same account** as the EcoFlow app.
3. Click **Become a Developer** and fill in the short form. For the purpose, something like "personal desktop widget for my own power station" is enough.
4. Wait for the approval email.
5. After approval, log in again and open **IoT Background** (top right), then **Security Information Management**.
6. Click **Create AccessKey**. A row with your Access Key appears.
7. Click the eye icon next to the dots to reveal the **Secret Key**.
8. Copy both keys into the widget's settings window.

## Keep the Secret Key private

- Never post it in chats, screenshots or issues. The Access Key alone is harmless, but together they give control over your station.
- If you think the Secret Key leaked, click **Delete** next to it on the portal, create a new pair and enter the new keys in the widget's Settings.

## Signed in with Google or Apple?

If you log in to the app with "Sign in with Google" or "Apple", EcoFlow can treat that as a separate account from an email-and-password login, even when the email looks the same. If the widget finds no devices, make sure the app and the developer portal use the same login method.
