import { mount } from 'svelte';
// Lettertypes zitten in de bundel: de sim (en de .exe) werkt zonder internet
import '@fontsource/barlow/latin-400.css';
import '@fontsource/barlow/latin-500.css';
import '@fontsource/barlow/latin-600.css';
import '@fontsource/barlow-condensed/latin-500.css';
import '@fontsource/barlow-condensed/latin-600.css';
import '@fontsource/barlow-condensed/latin-700.css';
import './app.css';
import App from './App.svelte';

export default mount(App, { target: document.getElementById('app') });
