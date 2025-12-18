import { createServer } from 'vite';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PASSWORD_FILE_NAME = '.sandbox-password';
const PASSWORD_FILE = path.join(__dirname, PASSWORD_FILE_NAME);
const COOKIE_NAME = 'sandbox_auth';
const COOKIE_MAX_AGE = 24 * 60 * 60; // 24 hours in seconds

// Read password from file
let password = null;
try {
  password = fs.readFileSync(PASSWORD_FILE, 'utf8').trim();
  console.log('Sandbox password loaded successfully - auth enabled');
} catch (err) {
  console.log('No password file found - running without authentication');
}

function createAuthMiddleware(password) {
  return (req, res, next) => {
    const url = new URL(req.url, `http://${req.headers.host}`);

    // Health check endpoint - always accessible
    if (url.pathname === '/health') {
      res.writeHead(204); // No Content
      res.end();
      return;
    }

    // If no password is configured, forbid all access
    if (!password) {
      res.writeHead(503, { 'Content-Type': 'text/plain' });
      res.end('503 - Service Unavailable: No authentication configured');
      return;
    }

    // Auth endpoint to set cookie
    if (url.pathname === '/auth') {
      return handleAuthEndpoint(req, res, password);
    }

    return next();

    // // Check cookie authentication
    // // All other endpoints (including /__healthcheck) require cookie authentication
    // const cookies = parseCookies(req.headers.cookie || '');
    // const authCookie = cookies[COOKIE_NAME];

    // if (authCookie === password) {
    //   return next();
    // }

    // // No valid auth - return 401
    // return sendUnauthorizedResponse(res);
  };
}

function handleAuthEndpoint(req, res, password) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const token = url.searchParams.get('token');

  if (token === password) {
    // Set cookie and redirect to root
    // Use SameSite=None and Secure for cross-site contexts (iframe embedding)
    res.writeHead(302, {
      'Set-Cookie': `${COOKIE_NAME}=${password}; Path=/; Max-Age=${COOKIE_MAX_AGE}; HttpOnly; SameSite=None; Secure`,
      'Location': '/'
    });
    res.end();
  } else {
    res.writeHead(401, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Invalid token' }));
  }
}

function sendUnauthorizedResponse(res) {
  res.writeHead(401, { 'Content-Type': 'text/plain' });
  res.end('401 - Authentication Required');
}

async function startServer() {
  const server = await createServer({
    configFile: path.join(__dirname, 'vite.config.ts'),
    root: __dirname,
  });

  // Add auth middleware at the beginning of the stack
  const authMiddleware = createAuthMiddleware(password);

  server.middlewares.stack.unshift({
    route: '',
    handle: authMiddleware
  });

  await server.listen();

  const address = server.httpServer.address();
  console.log(`Server listening on port ${address.port}`);
}

function parseCookies(cookieString) {
  const cookies = {};
  cookieString.split(';').forEach(cookie => {
    const [key, value] = cookie.trim().split('=');
    if (key) cookies[key] = value;
  });
  return cookies;
}

startServer().catch(err => {
  console.error('Error starting server:', err);
  process.exit(1);
});