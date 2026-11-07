# Changelog

## 0.1.2

- The ID tag option is now labelled "CircleLink station name" on the
  Configuration tab, in English and Lithuanian. It must exactly match the
  station name you gave the charger in the CircleLink app. The default is
  still `TESLA`.

## 0.1.1

- The app now has its own AppArmor profile. It may only read its own code,
  read and write `/data`, and make network connections. This raises its
  security rating in Home Assistant from 7 to 8.

## 0.1.0

- First version. Reports Wall Connector charging sessions, energy and status
  to an OCPP 1.6J backend such as CircleLink.
