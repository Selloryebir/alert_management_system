import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";

import { setCsrfToken } from "../src/api";

beforeEach(() => {
  setCsrfToken({ token: "test-csrf", header_name: "X-CSRF-TOKEN", parameter_name: "_csrf" });
});
