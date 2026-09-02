# Domain + SSL Setup Guide

## Overview

The Launchpad Call Center uses three domains:
- `api.launchpad.com` - Backend API
- `app.launchpad.com` - Frontend
- `ws.launchpad.com` - WebSocket

## DNS Configuration

### Route 53 Records

| Domain | Type | Value |
|--------|------|-------|
| api.launchpad.com | A | ALB Ingress IP |
| app.launchpad.com | A | ALB Ingress IP |
| ws.launchpad.com | A | ALB Ingress IP |

### Steps

1. Go to AWS Route 53
2. Select hosted zone for `launchpad.com`
3. Create A records for each subdomain pointing to the ALB

## SSL Certificate

### Option 1: Let's Encrypt (cert-manager)

The Kubernetes ingress is configured to use cert-manager with Let's Encrypt:

```yaml
cert-manager.io/cluster-issuer: "letsencrypt-prod"
```

cert-manager will automatically:
1. Request certificates from Let's Encrypt
2. Store them as Kubernetes secrets
3. Auto-renew before expiry

### Option 2: AWS Certificate Manager (ACM)

1. Go to AWS Certificate Manager
2. Request certificate for `*.launchpad.com`
3. Validate via DNS
4. Update ingress to use ACM certificate ARN

## Verification

After DNS propagation (up to 48 hours):

```bash
# Test API
curl https://api.launchpad.com/health

# Test Frontend
curl https://app.launchpad.com

# Test WebSocket
wscat -c wss://ws.launchpad.com/socket.io/?transport=websocket
```

## Troubleshooting

### Certificate not issued
- Check cert-manager logs: `kubectl logs -n cert-manager deployment/cert-manager`
- Verify ClusterIssuer: `kubectl get clusterissuer`
- Check certificate status: `kubectl get certificate -n launchpad`

### DNS not resolving
- Verify Route 53 records
- Check ALB is created: `aws elbv2 describe-load-balancers`
- Wait for DNS propagation (up to 48 hours)
