import { redirect } from "next/navigation";

/**
 * Root route — immediately redirects to the main story page.
 * All story UI lives at /story.
 */
export default function Home() {
  redirect("/story");
}
