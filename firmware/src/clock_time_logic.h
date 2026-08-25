#pragma once

#include <stdint.h>
#include <time.h>

struct ClockTimeFields {
  int16_t year;
  int8_t month;
  int8_t date;
  int8_t week_day;
  int8_t hours;
  int8_t minutes;
  int8_t seconds;
};

struct ClockSyncEpochs {
  int64_t utc_epoch;
  int64_t local_epoch;
};

inline bool clockRetainedRtcPlausible(const ClockTimeFields& fields) {
  if (fields.year < 2024 || fields.year > 2099 ||
      fields.month < 1 || fields.month > 12 ||
      fields.week_day < 0 || fields.week_day > 6 ||
      fields.hours < 0 || fields.hours > 23 ||
      fields.minutes < 0 || fields.minutes > 59 ||
      fields.seconds < 0 || fields.seconds > 59) {
    return false;
  }
  static const uint8_t daysPerMonth[] = {
    31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31
  };
  uint8_t maxDate = daysPerMonth[fields.month - 1];
  const bool leapYear = fields.year % 4 == 0 &&
    (fields.year % 100 != 0 || fields.year % 400 == 0);
  if (fields.month == 2 && leapYear) maxDate = 29;
  return fields.date >= 1 && fields.date <= maxDate;
}

inline bool clockRtcTrustAfterRefresh(
  bool alreadyTrusted,
  const ClockTimeFields& fields
) {
  return alreadyTrusted || clockRetainedRtcPlausible(fields);
}

inline constexpr ClockSyncEpochs clockSyncEpochs(
  int64_t utc_epoch,
  int32_t timezone_offset_seconds
) {
  return {utc_epoch, utc_epoch + timezone_offset_seconds};
}

inline bool clockFieldsFromLocalEpoch(int64_t local_epoch, ClockTimeFields* out) {
  if (!out) return false;
  time_t raw = (time_t)local_epoch;
  struct tm local_tm;
  if (!gmtime_r(&raw, &local_tm)) return false;
  out->year = (int16_t)(local_tm.tm_year + 1900);
  out->month = (int8_t)(local_tm.tm_mon + 1);
  out->date = (int8_t)local_tm.tm_mday;
  out->week_day = (int8_t)local_tm.tm_wday;
  out->hours = (int8_t)local_tm.tm_hour;
  out->minutes = (int8_t)local_tm.tm_min;
  out->seconds = (int8_t)local_tm.tm_sec;
  return true;
}
