const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function sendMessage(message) {
  let response;
  try {
    response = await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
  } catch {
    throw new Error("Can't reach the server. Is the backend running?");
  }

  if (!response.ok) {
    throw new Error(`Server error (${response.status}). Please try again.`);
  }

  const data = await response.json();
  return data.reply;
}