#pragma once

struct AboutInfo {
  const char* made_by;
  const char* source_line_1;
  const char* source_line_2;
  const char* hardware_line_1;
  const char* hardware_line_2;
};

inline AboutInfo currentAboutInfo() {
  return {
    "Ray",
    "OpenCode Buddy",
    "firmware fork",
    "M5Stick S3",
    "ESP32-S3 + M5PM1",
  };
}
