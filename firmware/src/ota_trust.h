#pragma once

#ifndef OPENCODE_BUDDY_OTA_ENABLED
#define OPENCODE_BUDDY_OTA_ENABLED 0
#endif

#if OPENCODE_BUDDY_OTA_ENABLED
#include "ota_trust_generated.h"
#ifndef OPENCODE_BUDDY_OTA_TRUST_GENERATED
#error "OTA is enabled but generated public trust is unavailable"
#endif
static_assert(sizeof(OPENCODE_BUDDY_OTA_CA_PEM) > 64,
              "OTA CA certificate is missing or malformed");
static_assert(sizeof(OPENCODE_BUDDY_OTA_MANIFEST_PUBLIC_KEY_PEM) > 64,
              "OTA manifest public key is missing or malformed");
#endif

inline const char* otaTrustLocalCaPem() {
#if OPENCODE_BUDDY_OTA_ENABLED
  return OPENCODE_BUDDY_OTA_CA_PEM;
#else
  return "";
#endif
}

inline const char* otaTrustManifestPublicKeyPem() {
#if OPENCODE_BUDDY_OTA_ENABLED
  return OPENCODE_BUDDY_OTA_MANIFEST_PUBLIC_KEY_PEM;
#else
  return "";
#endif
}
