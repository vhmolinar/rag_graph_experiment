import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "../src/App";

describe("App", () => {
  it("renderiza o título do produto", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "RAG de Livros" })).toBeTruthy();
  });
});
