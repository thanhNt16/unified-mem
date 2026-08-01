/** Sample TypeScript module for code_index tests. */

import { request } from "./client";

export class Api {
  /** Fetch a user by ID. */
  async fetchUser(id: string): Promise<{ name: string }> {
    const response = await fetch(`/api/users/${id}`);
    return response.json();
  }
}

export async function fetchData(): Promise<void> {
  const api = new Api();
  const user = await api.fetchUser("123");
  console.log(user.name);
}
