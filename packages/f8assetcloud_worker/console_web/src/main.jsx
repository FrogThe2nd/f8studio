import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConsoleRootApp } from './App.jsx';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ConsoleRootApp />
  </React.StrictMode>,
);
