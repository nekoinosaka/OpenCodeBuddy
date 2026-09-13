#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "about_info.h"

static void expect_str_eq(const char* actual, const char* expected, const char* message) {
  if (strcmp(actual, expected) != 0) {
    fprintf(stderr, "%s: expected '%s' got '%s'\n", message, expected, actual);
    exit(1);
  }
}

int main() {
  AboutInfo info = currentAboutInfo();

  expect_str_eq(info.made_by, "Ray", "about page should show the requested maker name");
  expect_str_eq(info.source_line_1, "OpenOpenCode Buddy", "about page source should match the project name");
  expect_str_eq(info.source_line_2, "firmware fork", "about page should describe the firmware origin");
  expect_str_eq(info.hardware_line_1, "M5Stick S3", "about page should name the StickS3 hardware");
  expect_str_eq(info.hardware_line_2, "ESP32-S3 + M5PM1", "about page should show StickS3 platform details");

  return 0;
}
