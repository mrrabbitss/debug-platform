#ifndef WIFI_CONFIG_H
#define WIFI_CONFIG_H
#define BAND_24G 24
#define BAND_5G 50
typedef struct { int band; int channel; char ssid[33]; } wifi_profile_t;
int apply_wifi_profile(const wifi_profile_t *profile);
#endif
