// Information for LLM Agent
// This is a component that handles errors in the application.
// don't update this file!

import { SnErrorBoundary } from "./snErrorBoundary";
import { SnMessageReceiver } from "./snMessageReceiver";

export const SnPrototype = ({ children }: { children: React.ReactNode }) => {
  return (
    <SnErrorBoundary>
      <SnMessageReceiver>{children}</SnMessageReceiver>
    </SnErrorBoundary>
  );
};
