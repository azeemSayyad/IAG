# A2P 10DLC And Engage Clouds Compliance Guide

## Overview

A2P 10DLC registration is required for compliant US SMS traffic on standard 10-digit numbers. Launchpad uses Engage Clouds as the primary communication provider, so brand, campaign, sender, and webhook setup should be completed through Engage Clouds or the provider-side carrier registration flow assigned to the agency.

## Registration Steps

### Step 1: Register Brand

Provide business information in Engage Clouds or its carrier registration portal:

- Legal company name
- EIN / Tax ID
- Business type
- Address
- Website
- Contact information

### Step 2: Register Campaign

- Select the correct use case, such as mixed informational and marketing.
- Document expected message volume.
- Submit sample messages and opt-out/help language.
- Wait for carrier approval before enabling production outbound traffic.

### Step 3: Configure Sender Numbers

- Assign approved sender numbers in Engage Clouds.
- Put those numbers in `ENGAGECLOUD_FROM_NUMBERS`.
- Configure the messaging webhook:
  - `https://api.launchpad.com/api/v1/webhooks/engage-clouds`

### Step 4: Update Production Environment

```bash
ENGAGECLOUD_FROM_NUMBERS=+1XXXXXXXXXX,+1YYYYYYYYYY
ENGAGE_CLOUD_WEBHOOK_SECRET=replace-with-provider-webhook-secret
```

## Compliance Requirements

- Obtain explicit consent before sending messages.
- Honor opt-out requests immediately.
- Send only during compliant hours for the recipient timezone.
- Maintain a suppression list.
- Include business identity and opt-out instructions where required.
- Avoid prohibited content and misleading urgency.

## Testing

1. Send a test SMS through Engage Clouds.
2. Verify the backend persists the outbound message with provider `engage_cloud`.
3. Verify Engage Clouds delivery webhook reaches `/api/v1/webhooks/engage-clouds`.
4. Send an inbound reply and verify conversation persistence plus websocket update.
5. Test STOP/START handling and suppression behavior.

## Production Checklist

- [ ] Brand approved
- [ ] Campaign approved
- [ ] Sender numbers assigned in Engage Clouds
- [ ] `ENGAGECLOUD_FROM_NUMBERS` configured
- [ ] Webhook URL configured
- [ ] Webhook secret configured
- [ ] TCPA compliance implemented
- [ ] Suppression list active
- [ ] Business hours checks active
- [ ] Opt-out handling tested
- [ ] Rate limiting active
- [ ] Monitoring configured
